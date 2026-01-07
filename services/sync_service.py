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
    
    def format_content(self, summary: Optional[Dict[str, Any]]) -> str:
        """
        Format Fireflies summary, notes, and action items.
        Does NOT include full transcript - only structured content.
        """
        if not summary:
            return ""
        
        sections = []
        
        # Overview/Summary
        overview = summary.get("overview")
        if overview:
            sections.append(f"SUMMARY:\n{overview}")
        
        # Outline/Notes
        outline = summary.get("outline")
        if outline:
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
            
            # If no company and no contacts, skip
            if not company_id and not contact_ids:
                logger.warning("SKIPPED: No company found and no existing contacts")
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="skipped",
                    reason="No company found and no existing contacts"
                )
            
            # Search for deals
            deal_ids = []
            found_deals = []
            
            if company_id:
                logger.deal(f"Searching for deals by company ID: {company_id}")
                deal_rows = dealcloud_client.search_deals_by_company(company_id)
                
                if deal_rows:
                    logger.success(f"Found {len(deal_rows)} deal(s)")
                    for deal in deal_rows:
                        deal_id = deal.get("EntryId")
                        deal_name = deal.get("DealName", "Unknown")
                        
                        if deal_id and deal_id not in deal_ids:
                            deal_ids.append(deal_id)
                            found_deals.append({
                                "name": deal_name,
                                "id": deal_id
                            })
                            logger.deal(f"  {deal_name} [ID: {deal_id}]")
                else:
                    logger.info("No deals found for this company")
            
            # Build interaction content
            participants_list = "\n".join(all_participants)
            summary = transcript.get("summary")
            content = self.format_content(summary)
            
            interaction_subject = f"Call: {title}"
            interaction_notes = (
                f"Fireflies Call Recording\n\n"
                f"Date: {transcript.get('date', 'Unknown')}\n"
                f"Duration: {transcript.get('duration', 0)} seconds\n\n"
                f"Participants:\n{participants_list}"
            )
            
            if content:
                interaction_notes += f"\n\n{content}"
            
            # Check for existing interaction
            logger.search("Checking for existing interaction...")
            existing = dealcloud_client.search_interaction_by_subject(interaction_subject)
            
            if existing:
                entry_id = existing.get("EntryId")
                logger.info(f"Interaction already exists (ID: {entry_id}) - SKIPPING")
                
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="skipped",
                    reason="Interaction already exists",
                    interaction_id=entry_id,
                    company_id=company_id,
                    contact_ids=contact_ids,
                    deal_ids=deal_ids,
                    created_contacts=created_contacts
                )
            
            # Create new interaction
            logger.interaction("Creating new interaction in DealCloud...")
            
            result = dealcloud_client.create_interaction(
                subject=interaction_subject,
                notes=interaction_notes,
                contact_ids=contact_ids,
                company_id=company_id,
                deal_ids=deal_ids
            )
            
            if result:
                logger.success(f"Interaction created (ID: {result.get('EntryId')})")
                
                return SyncResult(
                    transcript_id=transcript_id,
                    transcript_title=title,
                    status="created",
                    interaction_id=result.get("EntryId"),
                    company_id=company_id,
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
                    company_id=company_id,
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
    
    def sync_all(self, processed_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
        """
        Run full sync: fetch all new transcripts and process them.
        
        Args:
            processed_ids: Set of already processed transcript IDs
            
        Returns:
            Summary of sync operation
        """
        processed_ids = processed_ids or set()
        
        logger.separator("=", 60)
        logger.sync("STARTING FIREFLIES TO DEALCLOUD SYNC")
        logger.separator("=", 60)
        
        start_time = datetime.now()
        
        # Clear cache for fresh data
        dealcloud_client.clear_cache()
        
        # Fetch transcripts
        logger.outgoing("Fetching transcripts from Fireflies...")
        transcripts = fireflies_client.fetch_transcripts()
        
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
        skipped_count = sum(1 for r in results if r["status"] == "skipped")
        error_count = sum(1 for r in results if r["status"] == "error")
        contacts_created = sum(len(r.get("created_contacts", [])) for r in results)
        
        logger.separator("=", 60)
        logger.sync("SYNC COMPLETE")
        logger.separator("=", 60)
        logger.info(f"Total processed: {len(results)}")
        logger.success(f"Created: {created_count}")
        logger.info(f"Skipped: {skipped_count}")
        if error_count:
            logger.error(f"Errors: {error_count}")
        if contacts_created:
            logger.contact(f"Contacts created: {contacts_created}")
        logger.info(f"Duration: {duration:.1f} seconds")
        
        return {
            "success": True,
            "processed_count": len(results),
            "created_count": created_count,
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

