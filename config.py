# config.py
# Environment-based configuration with validation
# NEVER hardcode secrets - use environment variables!

import os
from typing import Optional


class Config:
    """Configuration management from environment variables"""
    
    def __init__(self):
        # Required variables - will raise on startup if missing
        self.FIREFLIES_API_KEY = self._get_required("FIREFLIES_API_KEY")
        self.DEALCLOUD_CLIENT_ID = self._get_required("DEALCLOUD_CLIENT_ID")
        self.DEALCLOUD_API_KEY = self._get_required("DEALCLOUD_API_KEY")
        
        # Optional with defaults
        self.DEALCLOUD_BASE_URL = os.getenv("DEALCLOUD_BASE_URL", "https://valescoind.dealcloud.com")
        self.FIREFLIES_API_URL = os.getenv("FIREFLIES_API_URL", "https://api.fireflies.ai/graphql")
        
        # Entry Type IDs (can be overridden via env vars)
        self.INTERACTION_ENTRY_TYPE_ID = int(os.getenv("INTERACTION_ENTRY_TYPE_ID", "20843"))
        self.CONTACT_ENTRY_TYPE_ID = int(os.getenv("CONTACT_ENTRY_TYPE_ID", "20841"))
        self.DEAL_ENTRY_TYPE_ID = int(os.getenv("DEAL_ENTRY_TYPE_ID", "20866"))
        self.INTERACTION_TYPE_ID = int(os.getenv("INTERACTION_TYPE_ID", "1419522"))
        
        # Internal domains (comma-separated in env var)
        internal_domains_str = os.getenv("INTERNAL_DOMAINS", "valescoind.com,gmail.com,outlook.com,yahoo.com")
        self.INTERNAL_DOMAINS = [d.strip().lower() for d in internal_domains_str.split(",")]
        
        # Sync settings
        self.TRANSCRIPT_LIMIT = int(os.getenv("TRANSCRIPT_LIMIT", "10"))
        self.RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "0.3"))
        self.BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))
        self.CRON_INTERVAL_MINUTES = int(os.getenv("CRON_INTERVAL_MINUTES", "360"))  # 6 hours
        
        # API settings
        self.API_TIMEOUT = int(os.getenv("API_TIMEOUT", "30"))
        self.MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
        self.RETRY_DELAY = float(os.getenv("RETRY_DELAY", "2.0"))
        
        # Environment info
        self.ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
        self.DEBUG = os.getenv("DEBUG", "false").lower() == "true"
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        
        # API key for protecting endpoints (optional but recommended)
        self.API_KEY = os.getenv("API_KEY", None)
    
    def _get_required(self, name: str) -> str:
        """Get required environment variable or raise"""
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Required environment variable '{name}' is not set")
        return value
    
    def get_status(self) -> dict:
        """Return configuration status for debugging (no secrets!)"""
        return {
            "environment": self.ENVIRONMENT,
            "debug": self.DEBUG,
            "dealcloud_base_url": self.DEALCLOUD_BASE_URL,
            "fireflies_api_url": self.FIREFLIES_API_URL,
            "transcript_limit": self.TRANSCRIPT_LIMIT,
            "rate_limit_delay": self.RATE_LIMIT_DELAY,
            "cron_interval_minutes": self.CRON_INTERVAL_MINUTES,
            "api_key_configured": bool(self.API_KEY),
            "internal_domains": self.INTERNAL_DOMAINS,
            "entry_type_ids": {
                "interaction": self.INTERACTION_ENTRY_TYPE_ID,
                "contact": self.CONTACT_ENTRY_TYPE_ID,
                "deal": self.DEAL_ENTRY_TYPE_ID
            }
        }


# Singleton instance
config = Config()

