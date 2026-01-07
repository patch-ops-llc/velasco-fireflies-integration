# services/dealcloud_client.py
# DealCloud API Client with OAuth Token Management
# Handles all DealCloud API operations

import time
import json
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import config
from logger import logger


class DealCloudClient:
    """
    Client for DealCloud REST API.
    
    Handles:
    - OAuth 2.0 token management (client_credentials)
    - Rate limiting with automatic delays
    - Contact CRUD operations
    - Interaction CRUD operations
    - Company/Deal lookups
    """
    
    def __init__(self):
        self.base_url = config.DEALCLOUD_BASE_URL
        self.client_id = config.DEALCLOUD_CLIENT_ID
        self.api_key = config.DEALCLOUD_API_KEY
        self.timeout = config.API_TIMEOUT
        self.rate_limit_delay = config.RATE_LIMIT_DELAY
        
        # Token management
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        # Search cache for performance
        self._cache: Dict[str, Any] = {}
        
        # Session with retry logic
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create session with retry logic"""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=config.MAX_RETRIES,
            backoff_factor=config.RETRY_DELAY,
            status_forcelist=[500, 502, 503, 504],  # Don't retry 429 - handle manually
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _delay(self):
        """Apply rate limiting delay"""
        time.sleep(self.rate_limit_delay)
    
    def _get_access_token(self) -> str:
        """
        Get valid access token, refreshing if needed.
        Uses OAuth 2.0 client_credentials grant.
        """
        # Check if we have a valid token
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(minutes=5):
                return self._access_token
        
        logger.config("Authenticating with DealCloud...")
        
        try:
            response = self.session.post(
                url=f"{self.base_url}/api/rest/v1/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.api_key,
                    "scope": "data"
                },
                timeout=self.timeout
            )
            
            if not response.ok:
                raise Exception(f"Auth failed: {response.status_code} - {response.text}")
            
            data = response.json()
            self._access_token = data.get("access_token")
            
            # Set expiry (typically 1 hour, but use returned value if available)
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            if not self._access_token:
                raise Exception("No access token in response")
            
            logger.success("DealCloud authenticated successfully")
            return self._access_token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"DealCloud authentication failed: {str(e)}", e)
            raise
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authenticated headers"""
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json"
        }
    
    def _handle_rate_limit(self, response: requests.Response) -> bool:
        """
        Handle rate limiting (429 responses).
        Returns True if request should be retried.
        """
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 3))
            logger.warning(f"Rate limited, waiting {retry_after} seconds...")
            time.sleep(retry_after)
            return True
        return False
    
    def clear_cache(self):
        """Clear the search cache"""
        self._cache = {}
        logger.config("DealCloud cache cleared")
    
    # ==================== Contact Operations ====================
    
    def search_contacts_by_email(self, emails: List[str]) -> List[Dict[str, Any]]:
        """
        Search for contacts by email addresses.
        
        Args:
            emails: List of email addresses to search
            
        Returns:
            List of matching contact records
        """
        if not emails:
            return []
        
        # Check cache
        cache_key = f"contacts:{','.join(sorted(emails))}"
        if cache_key in self._cache:
            logger.debug(f"Using cached results for {len(emails)} email(s)")
            return self._cache[cache_key]
        
        self._delay()
        
        query = {"Email": {"$in": emails}}
        logger.search(f"Searching contacts by email: {len(emails)} address(es)")
        
        try:
            response = self.session.get(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/contact",
                params={
                    "wrapIntoArrays": "true",
                    "query": json.dumps(query)
                },
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.search_contacts_by_email(emails)  # Retry
            
            if not response.ok:
                logger.warning(f"Contact search error: {response.status_code}")
                self._cache[cache_key] = []
                return []
            
            data = response.json()
            rows = data.get("rows", [])
            
            logger.success(f"Found {len(rows)} contact(s)")
            self._cache[cache_key] = rows
            return rows
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Contact search failed: {str(e)}", e)
            self._cache[cache_key] = []
            return []
    
    def create_contact(self, email: str, company_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Create a new contact in DealCloud.
        
        Args:
            email: Contact email address
            company_id: Optional company ID to associate
            
        Returns:
            Created contact data or None
        """
        # Company is required for DealCloud contacts
        if not company_id:
            logger.error("Cannot create contact - Company is a required field")
            return None
        
        self._delay()
        
        # Parse name from email
        email_prefix = email.split("@")[0]
        name_parts = email_prefix.replace(".", " ").replace("_", " ").replace("-", " ").split()
        first_name = name_parts[0].capitalize() if name_parts else "Unknown"
        last_name = name_parts[-1].capitalize() if len(name_parts) > 1 else "Contact"
        
        if first_name == last_name:
            last_name = "Contact"
        
        logger.contact(f"Creating contact: {first_name} {last_name} ({email})")
        
        payload = [{
            "Email": email,
            "FirstName": first_name,
            "LastName": last_name,
            "Company": [company_id]
        }]
        
        try:
            response = self.session.post(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/{config.CONTACT_ENTRY_TYPE_ID}",
                params={"unflatten": "yes"},
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.create_contact(email, company_id)  # Retry
            
            if not response.ok:
                logger.error(f"Contact creation error: {response.status_code} - {response.text[:200]}")
                return None
            
            data = response.json()
            result = data[0] if isinstance(data, list) and data else data
            
            entry_id = result.get("EntryId")
            
            # Check for creation errors
            if entry_id == -1 or result.get("Errors"):
                errors = result.get("Errors", [])
                error_desc = ", ".join([f"{e.get('field')}: {e.get('description')}" for e in errors])
                logger.error(f"Contact creation failed: {error_desc}")
                return None
            
            logger.success(f"Contact created (ID: {entry_id})")
            
            return {
                "EntryId": entry_id,
                "Email": email,
                "FirstName": first_name,
                "LastName": last_name,
                "Company": [{"id": company_id}]
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Contact creation failed: {str(e)}", e)
            return None
    
    # ==================== Interaction Operations ====================
    
    def search_interaction_by_subject(self, subject: str) -> Optional[Dict[str, Any]]:
        """
        Search for an existing interaction by subject.
        
        Args:
            subject: Interaction subject to search for
            
        Returns:
            Matching interaction or None
        """
        cache_key = f"interaction:{subject}"
        if cache_key in self._cache:
            logger.debug(f"Using cached interaction search for: {subject}")
            return self._cache[cache_key]
        
        self._delay()
        
        logger.search(f"Searching for interaction: {subject}")
        
        try:
            response = self.session.get(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/{config.INTERACTION_ENTRY_TYPE_ID}",
                params={
                    "wrapIntoArrays": "true",
                    "query": json.dumps({"Subject": subject})
                },
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.search_interaction_by_subject(subject)  # Retry
            
            if not response.ok:
                logger.warning(f"Interaction search error: {response.status_code}")
                self._cache[cache_key] = None
                return None
            
            data = response.json()
            rows = data.get("rows", [])
            
            if rows:
                existing = rows[0]
                logger.match(f"Found existing interaction (ID: {existing.get('EntryId')})")
                self._cache[cache_key] = existing
                return existing
            else:
                logger.info("No existing interaction found")
                self._cache[cache_key] = None
                return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Interaction search failed: {str(e)}", e)
            self._cache[cache_key] = None
            return None
    
    def create_interaction(
        self,
        subject: str,
        notes: str,
        contact_ids: List[int],
        company_id: Optional[int] = None,
        deal_ids: Optional[List[int]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new interaction in DealCloud.
        
        Args:
            subject: Interaction subject
            notes: Interaction notes/content
            contact_ids: List of contact IDs to associate
            company_id: Optional company ID
            deal_ids: Optional list of deal IDs
            
        Returns:
            Created interaction data or None
        """
        self._delay()
        
        logger.interaction(f"Creating interaction: {subject}")
        
        # Build payload with flat structure (unflatten=yes)
        payload = [{
            "Subject": subject,
            "Contacts": contact_ids,
            "Notes": notes,
            "Type": config.INTERACTION_TYPE_ID
        }]
        
        if company_id:
            payload[0]["Companies"] = [company_id]
        
        if deal_ids:
            payload[0]["Deals"] = deal_ids
        
        try:
            response = self.session.post(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/{config.INTERACTION_ENTRY_TYPE_ID}",
                params={"unflatten": "yes"},
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.create_interaction(subject, notes, contact_ids, company_id, deal_ids)
            
            logger.debug(f"Response status: {response.status_code}")
            
            if not response.ok:
                logger.error(f"Interaction creation error: {response.status_code} - {response.text[:300]}")
                return None
            
            data = response.json()
            result = data[0] if isinstance(data, list) and data else data
            
            entry_id = result.get("EntryId")
            
            if entry_id == -1 or result.get("Errors"):
                errors = result.get("Errors", [])
                error_desc = ", ".join([f"{e.get('field')}: {e.get('description')}" for e in errors])
                logger.error(f"Interaction creation failed: {error_desc}")
                return None
            
            logger.success(f"Interaction created (ID: {entry_id})")
            
            return {
                "EntryId": entry_id,
                "Subject": subject,
                "ContactIds": contact_ids,
                "CompanyId": company_id,
                "DealIds": deal_ids
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Interaction creation failed: {str(e)}", e)
            return None
    
    # ==================== Deal Operations ====================
    
    def search_deals_by_company(self, company_id: int) -> List[Dict[str, Any]]:
        """
        Search for deals associated with a company.
        
        Args:
            company_id: Company ID to search for
            
        Returns:
            List of matching deals
        """
        if not company_id:
            return []
        
        cache_key = f"deals_company:{company_id}"
        if cache_key in self._cache:
            logger.debug(f"Using cached deal search for company: {company_id}")
            return self._cache[cache_key]
        
        self._delay()
        
        logger.deal(f"Searching deals for company ID: {company_id}")
        
        try:
            response = self.session.get(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/deal",
                params={
                    "wrapIntoArrays": "true",
                    "query": json.dumps({"Company": company_id})
                },
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.search_deals_by_company(company_id)  # Retry
            
            if not response.ok:
                logger.warning(f"Deal search error: {response.status_code}")
                self._cache[cache_key] = []
                return []
            
            data = response.json()
            rows = data.get("rows", [])
            
            logger.success(f"Found {len(rows)} deal(s) for company")
            self._cache[cache_key] = rows
            return rows
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Deal search failed: {str(e)}", e)
            self._cache[cache_key] = []
            return []
    
    def search_deals_by_name(self, deal_name: str) -> List[Dict[str, Any]]:
        """
        Search for deals by name using contains/like matching.
        Used to find deals like "Project Rubicon" from call titles.
        
        Args:
            deal_name: Deal/project name to search for
            
        Returns:
            List of matching deals
        """
        if not deal_name:
            return []
        
        cache_key = f"deals_name:{deal_name.lower()}"
        if cache_key in self._cache:
            logger.debug(f"Using cached deal search for name: {deal_name}")
            return self._cache[cache_key]
        
        self._delay()
        
        logger.deal(f"Searching deals by name: {deal_name}")
        
        try:
            # Use $contains operator for partial matching
            response = self.session.get(
                url=f"{self.base_url}/api/rest/v4/data/entrydata/rows/deal",
                params={
                    "wrapIntoArrays": "true",
                    "query": json.dumps({"DealName": {"$contains": deal_name}})
                },
                headers=self._get_headers(),
                timeout=self.timeout
            )
            
            if self._handle_rate_limit(response):
                return self.search_deals_by_name(deal_name)  # Retry
            
            if not response.ok:
                logger.warning(f"Deal name search error: {response.status_code}")
                self._cache[cache_key] = []
                return []
            
            data = response.json()
            rows = data.get("rows", [])
            
            if rows:
                logger.success(f"Found {len(rows)} deal(s) matching '{deal_name}'")
            else:
                logger.info(f"No deals found matching '{deal_name}'")
            
            self._cache[cache_key] = rows
            return rows
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Deal name search failed: {str(e)}", e)
            self._cache[cache_key] = []
            return []
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test API connection by authenticating.
        
        Returns:
            Connection status dict
        """
        logger.config("Testing DealCloud API connection")
        
        try:
            token = self._get_access_token()
            if token:
                return {
                    "status": "connected",
                    "base_url": self.base_url
                }
            return {
                "status": "error",
                "error": "No token received"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }


# Singleton instance
dealcloud_client = DealCloudClient()

