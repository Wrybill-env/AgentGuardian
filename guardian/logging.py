"""
Logging configuration for Agent Guardian.
"""

import logging
import sys
from typing import Optional


def setup_logging(level: str = "INFO", redact_secrets: bool = True) -> logging.Logger:
    """Configure and return the guardian logger."""
    logger = logging.getLogger("guardian")
    logger.setLevel(getattr(logging, level.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler with custom formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    
    class RedactingFormatter(logging.Formatter):
        """Formatter that redacts sensitive information."""
        
        def __init__(self, *args, redact_secrets: bool = True, **kwargs):
            super().__init__(*args, **kwargs)
            self.redact_secrets = redact_secrets
        
        def format(self, record: logging.LogRecord) -> str:
            msg = super().format(record)
            if self.redact_secrets:
                # Redact tokens (alphanumeric strings that look like secrets)
                import re
                # Pattern: token= followed by alphanumeric chars
                msg = re.sub(r'token=([a-zA-Z0-9_-]+)', 'token=***REDACTED***', msg)
                # Pattern: secret= followed by alphanumeric chars
                msg = re.sub(r'secret=([a-zA-Z0-9_-]+)', 'secret=***REDACTED***', msg)
                # Pattern: "token": "value"
                msg = re.sub(r'"token"\s*:\s*"([^"]+)"', '"token": "***REDACTED***"', msg)
                # Pattern: "secret": "value"
                msg = re.sub(r'"secret"\s*:\s*"([^"]+)"', '"secret": "***REDACTED***"', msg)
            return msg
    
    formatter = RedactingFormatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
        redact_secrets=redact_secrets
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance."""
    if name:
        return logging.getLogger(f"guardian.{name}")
    return logging.getLogger("guardian")