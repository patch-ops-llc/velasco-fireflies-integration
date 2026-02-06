# services/sync_service.py
# Main sync orchestration for Fireflies → DealCloud integration

from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime

from config import config
from logger import logger
from services.fireflies_client import fireflies_client
from services.dealcloud_client import dealcloud_client


@dataclass
class SyncResult:
    """Result of a sync operation"""
    transcript_id: str
    transcript_title: Optional[str]
    status: str  # created, skipped, error
    reason: Optional[str] = None
    interaction_id: Optional[int] = None
    company_id: Optional[int] = None
    contact_ids: List[int] = field(default_factory=list)
    deal_ids: List[int] = field(default_factory=list)
    found_contacts: List[Dict] = field(default_factory=list)
    created_contacts: List[Dict] = field(default_factory=list)
    found_deals: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "transcript_title": self.transcript_title,
            "status": self.status,
            "reason": self.reason,
            "interaction_id": self.interaction_id,
            "company_id": self.company_id,
            "contact_ids": self.contact_ids,
            "deal_ids": self.deal_ids,
            "found_contacts": self.found_contacts,
            "created_contacts": self.created_contacts,
            "found_deals": self.found_deals,
            "error": self.error
        }


class SyncService:
    """
    Orchestrates synchronization between Fireflies and DealCloud.
    
    Workflow:
    1. Fetch transcripts from Fireflies
    2. Filter for new transcripts (not previously processed)
    3. For each transcript:
       - Extract external participants
       - Find/create contacts in DealCloud
       - Find associated deals
       - Create interaction with full content
    """
    
    def __init__(self):
        self.internal_domains = config.INTERNAL_DOMAINS
    
    def is_internal_email(self, email: str) -> bool:
        """Check if email belongs to an internal domain"""
        if not email or "@" not in email:
            return False
        domain = email.split("@")[1].lower()
        return domain in self.internal_domains
    
    def extract_domain(self, email: str) -> Optional[str]:
        """Extract domain from email address"""
        if not email or "@" not in email:
            return None
        return email.split("@")[1].lower()
    
    def extract_project_name(self, title: str) -> Optional[str]:
        """
        Extract project/deal name from call title.
        
        Common patterns:
        - "Project Rubicon - SPP / Valesco Discussion" → "Project Rubicon"
        - "Project Joy - S Group Capital Call" → "Project Joy"
        - "Honey - Pro Forma EBITDA" → "Honey"
        - "DME Opportunity: Valesco <> GCA" → None (no project name)
        
        Returns:
            Project name if found, None otherwise
        """
        if not title:
            return None
        
        import re
        
        # Pattern 1: "Project XYZ" anywhere in title
        project_match = re.search(r'(Project\s+\w+)', title, re.IGNORECASE)
        if project_match:
            return project_match.group(1)
        
        # Pattern 2: Title starts with a code name followed by separator
        # e.g., "Honey - Pro Forma EBITDA" or "Rubicon: Discussion"
        # Look for word(s) before common separators like " - ", " : ", " / "
        separator_match = re.match(r'^([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)?)\s*[-:/<>]', title)
        if separator_match:
            potential_name = separator_match.group(1).strip()
            # Filter out common non-project prefixes
            skip_words = ['call', 'meeting', 'discussion', 'touchbase', 'catch', 'internal', 
                          'weekly', 'daily', 'sync', 'update', 'review', 'valesco', 'team']
            if potential_name.lower() not in skip_words:
                return potential_name
        
        return None
    
    def _has_incomplete_notes(self, notes: str) -> bool:
        """
        Detect if interaction notes are incomplete (missing Fireflies summary data).
        
        This happens when Fireflies hasn't finished processing the transcript at the 
        time the interaction was first created. The interaction gets the basic header 
        (date, duration, participants) but no summary, detailed notes, or action items.
        
        Args:
            notes: The existing interaction's Notes field
            
        Returns:
            True if the notes appear incomplete and should be updated
        """
        if not notes or not notes.strip():
            return True
        
        # These markers indicate that full Fireflies summary content was included
        content_markers = ["SUMMARY:", "DETAILED NOTES:", "ACTION ITEMS:", "KEY TOPICS:", "NOTES:", "OUTLINE:"]
        
        has_any_content = any(marker in notes for marker in content_markers)
        
        if not has_any_content:
            logger.debug(f"Notes appear incomplete - no content markers found (length: {len(notes)} chars)")
            return True
        
        return False
    
    def format_content(self, summary: Optional[Dict[str, Any]]) -> str:
        """
        Format Fireflies summary, detailed notes, and action items.
        Includes shorthand_bullet for detailed meeting notes.
        Does NOT include full transcript - only structured content.
        """
        if not summary:
            return ""
        
        sections = []
        
        # Overview/Summary (brief)
        overview = summary.get("overview")
        if overview:
            sections.append(f"SUMMARY:\n{overview}")
        
        # Keywords/Topics (if available)
        keywords = summary.get("keywords")
        if keywords and isinstance(keywords, list) and keywords:
            keywords_text = ", ".join(keywords)
            sections.append(f"KEY TOPICS:\n{keywords_text}")
        
        # Detailed Notes (shorthand_bullet) - This is the detailed content!
        shorthand_bullet = summary.get("shorthand_bullet")
        if shorthand_bullet:
            sections.append(f"DETAILED NOTES:\n{shorthand_bullet}")
        
        # Outline/Notes (structured outline - fallback if no shorthand_bullet)
        outline = summary.get("outline")
        if outline:
            # If we have shorthand_bullet, label this as outline, otherwise as notes
            if shorthand_bullet:
                sections.append(f"OUTLINE:\n{outline}")
            else:
                sections.append(f"NOTES:\n{outline}")
        
        # Action Items
        action_items = summary.get("action_items")
        if action_items and isinstance(action_items, list) and action_items:
            items_text = "\n".join([f"  • {item}" for item in action_items])
            sections.append(f"ACTION ITEMS:\n{items_text}")
        
        return "\n\n".join(sections) if sections else ""
    
    def process_transcript(
        self,
        transcript: Dict[str, Any],
        processed_ids: Optional[Set[str]] = None
    ) -> SyncResult:
        """
        Process a single transcript and create interaction in DealCloud.
        
        Args:
            transcript: Fireflies transcript data
            processed_ids: Set of already processed transcript IDs
            
        Returns:
            SyncResult with outcome details
        """
        processed_ids = processed_ids or set()
        transcript_id = transcript.get("id", "")
        title = transcript.get("title", "Untitled")
        
        logger.separator("-", 50)
        logger.sync(f"Processing: {title}")
        logger.info(f"ID: {transcript_id}")
        logger.info(f"Date: {transcript.get('date', 'Unknown')}")
        
        try:
            # Extract participants
            all_participants = transcript.get("participants") or []
            all_emails = [p for p in all_participants if p and "@" in p]
            external_emails = [e for e in all_emails if not self.is_internal_email(e)]
            internal_emails = [e for e in all_emails if self.is_internal_email(e)]
            
            logger.info(f"Participants: Total={len(all_emails)}, External={len(external_emails)}, Internal={len(internal_emails)}")
            
            # Skip if no external participants
            if not external_emails:
                logger.warning("SKIPPED: No external participants")
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="skipped",
                    reason="No external participants"
                )
            
            unique_emails = list(set(external_emails))
            logger.info(f"External emails: {', '.join(unique_emails)}")
            
            # Search for existing contacts
            company_id = None
            contact_ids = []
            found_contacts = []
            created_contacts = []
            found_emails: Set[str] = set()
            
            logger.search("Searching for contacts by email...")
            contact_rows = dealcloud_client.search_contacts_by_email(unique_emails)
            
            if contact_rows:
                logger.success(f"Found {len(contact_rows)} existing contact(s)")
                
                for contact in contact_rows:
                    contact_id = contact.get("EntryId")
                    contact_email = contact.get("Email", "Unknown")
                    found_emails.add(contact_email.lower())
                    
                    # Extract name
                    fullname_obj = contact.get("FullName", {})
                    if isinstance(fullname_obj, dict):
                        contact_name = fullname_obj.get("name", "Unknown")
                    else:
                        contact_name = str(fullname_obj) if fullname_obj else "Unknown"
                    
                    if contact_id and contact_id not in contact_ids:
                        contact_ids.append(contact_id)
                        found_contacts.append({
                            "email": contact_email,
                            "name": contact_name,
                            "id": contact_id
                        })
                        logger.contact(f"  {contact_name} ({contact_email}) [ID: {contact_id}]")
                        
                        # Get company from first contact
                        if not company_id:
                            company_ref = contact.get("Company", [])
                            if isinstance(company_ref, list) and company_ref:
                                company_id = company_ref[0].get("id")
                                company_name = company_ref[0].get("name", "Unknown")
                                logger.company(f"  Associated company: {company_name} (ID: {company_id})")
            else:
                logger.info("No existing contacts found")
            
            # Create missing contacts (if we have a company)
            missing_emails = [e for e in unique_emails if e.lower() not in found_emails]
            
            if missing_emails:
                if company_id:
                    logger.contact(f"Creating {len(missing_emails)} new contact(s)...")
                    
                    for email in missing_emails:
                        new_contact = dealcloud_client.create_contact(email, company_id)
                        
                        if new_contact:
                            contact_id = new_contact.get("EntryId")
                            contact_ids.append(contact_id)
                            created_contacts.append({
                                "email": email,
                                "name": f"{new_contact.get('FirstName', '')} {new_contact.get('LastName', '')}".strip(),
                                "id": contact_id
                            })
                        else:
                            logger.warning(f"  Failed to create contact: {email}")
                else:
                    logger.warning(f"Cannot create {len(missing_emails)} contact(s) - Company required but not found")
            
            # Note: We proceed even without a company from contacts
            # because we may find a deal by project name and get the company from there
            if not company_id and not contact_ids:
                logger.warning("No company from contacts and no existing contacts found")
                logger.info("Will attempt to find deal by project name...")
            
            # Search for deals - PRIORITY: Search by project name from title first
            deal_ids = []
            found_deals = []
            target_company_id = None  # Company from the deal (target company, not banker)
            target_company_name = None
            
            # Step 1: Extract project name from call title and search by name
            project_name = self.extract_project_name(title)
            if project_name:
                logger.deal(f"Extracted project name from title: '{project_name}'")
                logger.deal(f"Searching for deals by project name...")
                deal_rows = dealcloud_client.search_deals_by_name(project_name)
                
                if deal_rows:
                    logger.success(f"Found {len(deal_rows)} deal(s) matching project name")
                    for deal in deal_rows:
                        deal_id = deal.get("EntryId")
                        deal_name = deal.get("DealName", "Unknown")
                        
                        if deal_id and deal_id not in deal_ids:
                            deal_ids.append(deal_id)
                            found_deals.append({
                                "name": deal_name,
                                "id": deal_id
                            })
                            logger.deal(f"  {deal_name} [ID: {deal_id}] (matched by project name)")
                            
                            # IMPORTANT: Extract target company from the deal
                            # This is the actual company (e.g., NEC Inc.), not the banker
                            if not target_company_id:
                                deal_company_ref = deal.get("Company", [])
                                if isinstance(deal_company_ref, list) and deal_company_ref:
                                    target_company_id = deal_company_ref[0].get("id")
                                    target_company_name = deal_company_ref[0].get("name", "Unknown")
                                    logger.company(f"  Deal's target company: {target_company_name} (ID: {target_company_id})")
                                elif isinstance(deal_company_ref, dict) and deal_company_ref:
                                    target_company_id = deal_company_ref.get("id")
                                    target_company_name = deal_company_ref.get("name", "Unknown")
                                    logger.company(f"  Deal's target company: {target_company_name} (ID: {target_company_id})")
            else:
                logger.warning(f"Could not extract project name from title: '{title}'")
            
            # Step 2: If no deals found by name and we have a company, search by company as fallback
            if not deal_ids and company_id:
                logger.deal(f"No deals found by project name, searching by company ID: {company_id}")
                deal_rows = dealcloud_client.search_deals_by_company(company_id)
                
                if deal_rows:
                    logger.success(f"Found {len(deal_rows)} deal(s) by company")
                    for deal in deal_rows:
                        deal_id = deal.get("EntryId")
                        deal_name = deal.get("DealName", "Unknown")
                        
                        if deal_id and deal_id not in deal_ids:
                            deal_ids.append(deal_id)
                            found_deals.append({
                                "name": deal_name,
                                "id": deal_id
                            })
                            logger.deal(f"  {deal_name} [ID: {deal_id}] (matched by company)")
                else:
                    logger.info("No deals found for this company")
            elif not deal_ids:
                logger.info("No deals found (no project name extracted and no company)")
            
            # Use target company from deal if found, otherwise use banker's company
            # This ensures Project Rubicon links to NEC Inc., not Crutchfield Capital
            final_company_id = target_company_id if target_company_id else company_id
            if target_company_id and company_id and target_company_id != company_id:
                logger.company(f"Using deal's target company ({target_company_name}) instead of contact's company")
            
            # Now check if we have enough to create an interaction
            if not final_company_id and not contact_ids and not deal_ids:
                logger.warning("SKIPPED: No company, no contacts, and no deals found")
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="skipped",
                    reason="No company, contacts, or deals found to link interaction"
                )
            
            # Build interaction content
            participants_list = "\n".join(all_participants)
            summary = transcript.get("summary")
            
            # Debug logging for summary content
            if summary:
                logger.info(f"Summary data received from Fireflies:")
                logger.info(f"  - overview: {'Yes' if summary.get('overview') else 'No'} ({len(summary.get('overview', '') or '')} chars)")
                logger.info(f"  - shorthand_bullet: {'Yes' if summary.get('shorthand_bullet') else 'No'} ({len(summary.get('shorthand_bullet', '') or '')} chars)")
                logger.info(f"  - outline: {'Yes' if summary.get('outline') else 'No'} ({len(summary.get('outline', '') or '')} chars)")
                logger.info(f"  - action_items: {'Yes' if summary.get('action_items') else 'No'} ({len(summary.get('action_items', []) or [])} items)")
                logger.info(f"  - keywords: {'Yes' if summary.get('keywords') else 'No'}")
            else:
                logger.warning("No summary data received from Fireflies - notes will be minimal!")
            
            content = self.format_content(summary)
            
            if content:
                logger.info(f"Formatted content length: {len(content)} characters")
            else:
                logger.warning("Formatted content is empty - interaction will have minimal notes")
            
            interaction_subject = f"Call: {title}"
            interaction_notes = (
                f"Fireflies Call Recording\n\n"
                f"Date: {transcript.get('date', 'Unknown')}\n"
                f"Duration: {transcript.get('duration', 0)} seconds\n\n"
                f"Participants:\n{participants_list}"
            )
            
            if content:
                interaction_notes += f"\n\n{content}"
            
            logger.info(f"Total interaction notes length: {len(interaction_notes)} characters")
            
            # Check for existing interaction
            logger.search("Checking for existing interaction...")
            existing = dealcloud_client.search_interaction_by_subject(interaction_subject)
            
            if existing:
                entry_id = existing.get("EntryId")
                existing_notes = existing.get("Notes") or ""
                
                # Detect if the existing interaction has incomplete notes
                # (created before Fireflies finished processing the summary)
                notes_incomplete = self._has_incomplete_notes(existing_notes)
                has_new_content = bool(content)  # Fireflies now has summary data
                
                if notes_incomplete and has_new_content:
                    logger.warning(f"Interaction exists (ID: {entry_id}) but notes are INCOMPLETE - updating with full content")
                    logger.info(f"  Existing notes length: {len(existing_notes)} chars")
                    logger.info(f"  New notes length: {len(interaction_notes)} chars")
                    
                    update_result = dealcloud_client.update_interaction(
                        entry_id=entry_id,
                        notes=interaction_notes,
                        contact_ids=contact_ids if contact_ids else None,
                        company_id=final_company_id if final_company_id else None,
                        deal_ids=deal_ids if deal_ids else None
                    )
                    
                    if update_result:
                        logger.success(f"Interaction updated with full notes (ID: {entry_id})")
                        return SyncResult(
                            transcript_id=transcript_id,
                            transcript_title=title,
                            status="updated",
                            reason="Notes backfilled (Fireflies summary now available)",
                            interaction_id=entry_id,
                            company_id=final_company_id,
                            contact_ids=contact_ids,
                            deal_ids=deal_ids,
                            found_contacts=found_contacts,
                            created_contacts=created_contacts,
                            found_deals=found_deals
                        )
                    else:
                        logger.error(f"Failed to update interaction (ID: {entry_id})")
                        return SyncResult(
                            transcript_id=transcript_id,
                            transcript_title=title,
                            status="error",
                            error="Failed to update interaction with backfilled notes",
                            interaction_id=entry_id,
                            company_id=final_company_id,
                            contact_ids=contact_ids,
                            deal_ids=deal_ids
                        )
                else:
                    if not notes_incomplete:
                        logger.info(f"Interaction already exists with complete notes (ID: {entry_id}) - SKIPPING")
                    else:
                        logger.info(f"Interaction exists (ID: {entry_id}), notes incomplete but Fireflies still has no summary - SKIPPING")
                    
                    return SyncResult(
                        transcript_id=transcript_id,
                        transcript_title=title,
                        status="skipped",
                        reason="Interaction already exists" if not notes_incomplete else "Interaction exists, Fireflies summary still pending",
                        interaction_id=entry_id,
                        company_id=final_company_id,
                        contact_ids=contact_ids,
                        deal_ids=deal_ids,
                        created_contacts=created_contacts
                    )
            
            # Create new interaction
            logger.interaction("Creating new interaction in DealCloud...")
            logger.interaction(f"  Subject: {interaction_subject}")
            logger.interaction(f"  Company ID: {final_company_id}")
            logger.interaction(f"  Contact IDs: {contact_ids}")
            logger.interaction(f"  Deal IDs: {deal_ids}")
            logger.interaction(f"  Notes length: {len(interaction_notes)} chars")
            
            result = dealcloud_client.create_interaction(
                subject=interaction_subject,
                notes=interaction_notes,
                contact_ids=contact_ids,
                company_id=final_company_id,
                deal_ids=deal_ids
            )
            
            if result:
                logger.success(f"Interaction created (ID: {result.get('EntryId')})")
                logger.success(f"  Linked to company: {final_company_id}")
                logger.success(f"  Linked to deals: {deal_ids}")
                
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="created",
                    interaction_id=result.get("EntryId"),
                    company_id=final_company_id,
                    contact_ids=contact_ids,
                    deal_ids=deal_ids,
                    found_contacts=found_contacts,
                    created_contacts=created_contacts,
                    found_deals=found_deals
                )
            else:
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="error",
                    error="Failed to create interaction",
                    company_id=final_company_id,
                    created_contacts=created_contacts
                )
            
        except Exception as e:
            logger.error(f"Error processing transcript: {str(e)}", e)
            return SyncResult(
                transcript_id=transcript_id,
                transcript_title=title,
                status="error",
                error=str(e)
            )
    
    def sync_all(self, processed_ids: Optional[Set[str]] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Run full sync: fetch all new transcripts and process them.
        
        Args:
            processed_ids: Set of already processed transcript IDs
            limit: Number of transcripts to fetch (default: from config)
            
        Returns:
            Summary of sync operation
        """
        processed_ids = processed_ids or set()
        
        logger.separator("=", 60)
        logger.sync("STARTING FIREFLIES TO DEALCLOUD SYNC")
        if limit:
            logger.sync(f"Fetching last {limit} transcripts")
        logger.separator("=", 60)
        
        start_time = datetime.now()
        
        # Clear cache for fresh data
        dealcloud_client.clear_cache()
        
        # Fetch transcripts
        logger.outgoing(f"Fetching transcripts from Fireflies (limit: {limit or 'default'})...")
        transcripts = fireflies_client.fetch_transcripts(limit=limit)
        
        if not transcripts:
            logger.warning("No transcripts retrieved from Fireflies")
            return {
                "success": True,
                "processed_count": 0,
                "results": [],
                "duration_seconds": 0
            }
        
        # Filter new transcripts
        new_transcripts = [t for t in transcripts if t.get("id") not in processed_ids]
        logger.info(f"Found {len(new_transcripts)} new transcripts (out of {len(transcripts)} total)")
        
        # Process each transcript
        results = []
        for idx, transcript in enumerate(new_transcripts, 1):
            logger.info(f"--- Transcript {idx}/{len(new_transcripts)} ---")
            result = self.process_transcript(transcript, processed_ids)
            results.append(result.to_dict())
        
        # Calculate stats
        duration = (datetime.now() - start_time).total_seconds()
        created_count = sum(1 for r in results if r["status"] == "created")
        updated_count = sum(1 for r in results if r["status"] == "updated")
        skipped_count = sum(1 for r in results if r["status"] == "skipped")
        error_count = sum(1 for r in results if r["status"] == "error")
        contacts_created = sum(len(r.get("created_contacts", [])) for r in results)
        
        logger.separator("=", 60)
        logger.sync("SYNC COMPLETE")
        logger.separator("=", 60)
        logger.info(f"Total processed: {len(results)}")
        logger.success(f"Created: {created_count}")
        if updated_count:
            logger.success(f"Updated (notes backfilled): {updated_count}")
        logger.info(f"Skipped: {skipped_count}")
        if error_count:
            logger.error(f"Errors: {error_count}")
        if contacts_created:
            logger.contact(f"Contacts created: {contacts_created}")
        logger.info(f"Duration: {duration:.1f} seconds")
        
        return {
            "success": True,
            "transcripts_fetched": len(transcripts),
            "transcripts_limit": limit or "default",
            "processed_count": len(results),
            "created_count": created_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "contacts_created": contacts_created,
            "duration_seconds": round(duration, 1),
            "results": results
        }
    
    def sync_transcript(self, transcript_id: str) -> Dict[str, Any]:
        """
        Sync a single transcript by ID.
        
        Args:
            transcript_id: Fireflies transcript ID
            
        Returns:
            Result of sync operation
        """
        logger.sync(f"Syncing single transcript: {transcript_id}")
        
        # Fetch transcript
        transcript = fireflies_client.fetch_transcript_by_id(transcript_id)
        
        if not transcript:
            return {
                "success": False,
                "error": f"Transcript not found: {transcript_id}"
            }
        
        result = self.process_transcript(transcript)
        
        return {
            "success": result.status != "error",
            "result": result.to_dict()
        }


# Singleton instance
sync_service = SyncService()

