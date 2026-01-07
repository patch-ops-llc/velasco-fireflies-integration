# services/fireflies_client.py
# Fireflies.ai API Client
# Handles GraphQL queries for transcript data

import time
import requests
from typing import List, Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import config
from logger import logger


class FirefliesClient:
    """
    Client for Fireflies.ai GraphQL API.
    
    Handles:
    - Transcript fetching with pagination
    - Rate limiting
    - Retry logic
    """
    
    def __init__(self):
        self.api_url = config.FIREFLIES_API_URL
        self.api_key = config.FIREFLIES_API_KEY
        self.timeout = config.API_TIMEOUT
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create session with retry logic"""
        session = requests.Session()
        
        # Configure retries
        retry_strategy = Retry(
            total=config.MAX_RETRIES,
            backoff_factor=config.RETRY_DELAY,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API headers"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
    
    def fetch_transcripts(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch transcripts from Fireflies.
        
        Args:
            limit: Maximum number of transcripts to fetch
            
        Returns:
            List of transcript objects
        """
        limit = limit or config.TRANSCRIPT_LIMIT
        
        logger.outgoing(f"Fetching up to {limit} transcripts from Fireflies")
        
        query = """
        query Transcripts($limit: Int!) {
          transcripts(limit: $limit) {
            id
            title
            date
            duration
            participants
            summary {
              overview
              shorthand_bullet
              outline
              action_items
              keywords
            }
          }
        }
        """
        
        try:
            response = self.session.post(
                url=self.api_url,
                headers=self._get_headers(),
                json={
                    "query": query,
                    "variables": {"limit": limit}
                },
                timeout=self.timeout
            )
            
            if not response.ok:
                logger.error(f"Fireflies API error: {response.status_code} - {response.text[:200]}")
                return []
            
            data = response.json()
            
            # Check for GraphQL errors
            if "errors" in data:
                logger.error(f"Fireflies GraphQL errors: {data['errors']}")
                return []
            
            transcripts = data.get("data", {}).get("transcripts", [])
            logger.success(f"Retrieved {len(transcripts)} transcripts from Fireflies")
            
            return transcripts
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Fireflies API request failed: {str(e)}", e)
            return []
    
    def fetch_transcript_by_id(self, transcript_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single transcript by ID.
        
        Args:
            transcript_id: The Fireflies transcript ID
            
        Returns:
            Transcript object or None
        """
        logger.search(f"Fetching transcript: {transcript_id}")
        
        query = """
        query Transcript($transcriptId: String!) {
          transcript(id: $transcriptId) {
            id
            title
            date
            duration
            participants
            summary {
              overview
              shorthand_bullet
              outline
              action_items
              keywords
            }
          }
        }
        """
        
        try:
            response = self.session.post(
                url=self.api_url,
                headers=self._get_headers(),
                json={
                    "query": query,
                    "variables": {"transcriptId": transcript_id}
                },
                timeout=self.timeout
            )
            
            if not response.ok:
                logger.error(f"Fireflies API error: {response.status_code}")
                return None
            
            data = response.json()
            
            if "errors" in data:
                logger.error(f"Fireflies GraphQL errors: {data['errors']}")
                return None
            
            transcript = data.get("data", {}).get("transcript")
            
            if transcript:
                logger.success(f"Found transcript: {transcript.get('title', 'Untitled')}")
            else:
                logger.warning(f"Transcript not found: {transcript_id}")
            
            return transcript
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Fireflies API request failed: {str(e)}", e)
            return None
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test API connection by fetching user info.
        
        Returns:
            Connection status dict
        """
        logger.config("Testing Fireflies API connection")
        
        query = """
        query User {
          user {
            email
            name
          }
        }
        """
        
        try:
            response = self.session.post(
                url=self.api_url,
                headers=self._get_headers(),
                json={"query": query},
                timeout=self.timeout
            )
            
            if response.ok:
                data = response.json()
                if "errors" not in data:
                    user = data.get("data", {}).get("user", {})
                    logger.success(f"Fireflies connected: {user.get('email', 'Unknown')}")
                    return {
                        "status": "connected",
                        "user": user
                    }
            
            return {
                "status": "error",
                "error": f"API returned {response.status_code}"
            }
            
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "error": str(e)
            }


# Singleton instance
fireflies_client = FirefliesClient()

