# logger.py
# Enhanced logging utility with emoji indicators for Railway logs
# Based on best practices from HubSpot-QuantumReverse integration

import logging
import sys
from datetime import datetime
from typing import Any, Optional


class Logger:
    """
    Enhanced logger with emoji indicators for easy log scanning.
    
    Emoji Reference:
        📥 Incoming request/data
        📤 Outgoing request/data
        ✅ Success
        ❌ Error
        ⚠️ Warning
        🔍 Lookup/Search operation
        ⏰ Scheduled/Time-based operation
        🔧 Configuration/Setup
        🔄 Sync operation
        👤 Contact operation
        🏢 Company operation
        📞 Interaction/Call operation
        💼 Deal operation
        🎯 Match found
    """
    
    def __init__(self, name: str = "fireflies-dealcloud"):
        self.logger = logging.getLogger(name)
        self._setup_logger()
    
    def _setup_logger(self):
        """Configure logging format and handlers"""
        # Only configure if not already configured
        if not self.logger.handlers:
            self.logger.setLevel(logging.DEBUG)
            
            # Console handler with formatting
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(logging.DEBUG)
            
            # Format: timestamp [LEVEL] message
            formatter = logging.Formatter(
                fmt="[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def _format_timestamp(self) -> str:
        """Get formatted timestamp"""
        return datetime.now().strftime("%H:%M:%S")
    
    # Standard log levels
    def debug(self, message: str, data: Optional[Any] = None):
        """Debug level logging with optional data dump"""
        self.logger.debug(f"🔍 {message}")
        if data:
            self._log_data(data)
    
    def info(self, message: str):
        """Info level logging"""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Warning level logging"""
        self.logger.warning(f"⚠️ {message}")
    
    def error(self, message: str, error: Optional[Exception] = None):
        """Error level logging with optional exception"""
        self.logger.error(f"❌ {message}")
        if error:
            self.logger.error(f"   Exception: {str(error)}")
    
    # Semantic log methods with emojis
    def success(self, message: str):
        """Success indicator"""
        self.logger.info(f"✅ {message}")
    
    def incoming(self, message: str):
        """Incoming request/data"""
        self.logger.info(f"📥 {message}")
    
    def outgoing(self, message: str):
        """Outgoing request/data"""
        self.logger.info(f"📤 {message}")
    
    def search(self, message: str):
        """Search/lookup operation"""
        self.logger.info(f"🔍 {message}")
    
    def sync(self, message: str):
        """Sync operation"""
        self.logger.info(f"🔄 {message}")
    
    def scheduled(self, message: str):
        """Scheduled/cron operation"""
        self.logger.info(f"⏰ {message}")
    
    def config(self, message: str):
        """Configuration message"""
        self.logger.info(f"🔧 {message}")
    
    def contact(self, message: str):
        """Contact operation"""
        self.logger.info(f"👤 {message}")
    
    def company(self, message: str):
        """Company operation"""
        self.logger.info(f"🏢 {message}")
    
    def interaction(self, message: str):
        """Interaction/call operation"""
        self.logger.info(f"📞 {message}")
    
    def deal(self, message: str):
        """Deal operation"""
        self.logger.info(f"💼 {message}")
    
    def match(self, message: str):
        """Match found"""
        self.logger.info(f"🎯 {message}")
    
    def separator(self, char: str = "=", length: int = 60):
        """Log a separator line"""
        self.logger.info(char * length)
    
    def _log_data(self, data: Any):
        """Log structured data"""
        try:
            import json
            if isinstance(data, (dict, list)):
                formatted = json.dumps(data, indent=2, default=str)
                for line in formatted.split("\n"):
                    self.logger.debug(f"   {line}")
            else:
                self.logger.debug(f"   {str(data)}")
        except Exception:
            self.logger.debug(f"   {str(data)}")


# Singleton instance
logger = Logger()
