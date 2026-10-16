"""
Configuration management for Guardian.

Supports loading from YAML file, environment variables, and defaults.
"""

import os
import yaml
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .types import SafetyMode
from .backoff import BackoffConfig, DEFAULT_BACKOFF


DEFAULT_CONFIG_PATH = Path.home() / ".guardian" / "config.yaml"


@dataclass
class GuardianConfig:
    """Complete Guardian configuration."""
    
    # Safety mode
    safety_mode: SafetyMode = SafetyMode.OBSERVE_ONLY
    
    # Discovery settings
    discovery_host: str = "127.0.0.1"
    discovery_port_range: tuple = (10000, 65535)
    discovery_timeout: float = 30.0
    discovery_concurrent: int = 50
    
    # Connection settings
    connection_timeout: float = 10.0
    handshake_timeout: float = 10.0
    ping_interval: float = 30.0
    ping_timeout: float = 10.0
    use_tls: bool = True
    
    # Stale detection
    stale_threshold: float = 60.0
    stale_check_interval: float = 10.0
    
    # Backoff configuration
    backoff: BackoffConfig = field(default_factory=lambda: DEFAULT_BACKOFF)
    
    # Safety settings
    require_human_for_ambiguous: bool = True
    max_recovery_attempts_per_failure: int = 3
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    redact_secrets: bool = True
    
    # State persistence
    persist_state: bool = False
    state_file: str = "~/.guardian/state.json"

    # Cross-process recovery exclusion. Default-on because it only ever
    # *restricts* recovery: an unusable lease directory denies recovery.
    recovery_lease_enabled: bool = True
    recovery_lease_dir: Optional[str] = None  # derived from state_file when None

    # Goose-specific
    goose_token: Optional[str] = None
    goose_port: Optional[int] = None

    def resolved_lease_dir(self) -> str:
        """Directory holding per-session recovery lease files."""
        if self.recovery_lease_dir:
            return str(Path(self.recovery_lease_dir).expanduser())
        return str(Path(self.state_file).expanduser().parent / "leases")

    def build_recovery_lease(self):
        """
        Build the cross-process recovery lease for automatic recovery, or None
        when disabled. A lease that cannot be created/acquired denies recovery
        (fail closed), so enabling it can only make recovery stricter.
        """
        if not self.recovery_lease_enabled:
            return None
        from .recovery_lease import RecoveryLease
        return RecoveryLease(self.resolved_lease_dir())
    
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GuardianConfig":
        """Load configuration from file, with env var overrides."""
        config = cls()
        
        # Load from file
        if path is None:
            path = DEFAULT_CONFIG_PATH
        
        if path and path.exists():
            with open(path, 'r') as f:
                data = yaml.safe_load(f) or {}
            config = cls._from_dict(data)
        
        # Override with environment variables
        config = cls._apply_env_overrides(config)
        
        return config
    
    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> "GuardianConfig":
        """Create config from dictionary."""
        config = cls()
        
        # Simple fields
        for key, value in data.items():
            if hasattr(config, key):
                if key == "safety_mode":
                    value = SafetyMode(value)
                elif key == "backoff":
                    if isinstance(value, dict):
                        value = BackoffConfig(**value)
                elif key == "discovery_port_range":
                    value = tuple(value)
                setattr(config, key, value)
        
        return config
    
    @classmethod
    def _apply_env_overrides(cls, config: "GuardianConfig") -> "GuardianConfig":
        """Apply environment variable overrides."""
        env_mapping = {
            "GUARDIAN_SAFETY_MODE": ("safety_mode", lambda v: SafetyMode(v)),
            "GUARDIAN_DISCOVERY_HOST": ("discovery_host", str),
            "GUARDIAN_DISCOVERY_TIMEOUT": ("discovery_timeout", float),
            "GUARDIAN_STALE_THRESHOLD": ("stale_threshold", float),
            "GUARDIAN_LOG_LEVEL": ("log_level", str),
            "GUARDIAN_REDACT_SECRETS": ("redact_secrets", lambda v: v.lower() == "true"),
            "GUARDIAN_GOOSE_TOKEN": ("goose_token", str),
            "GUARDIAN_GOOSE_PORT": ("goose_port", int),
            "GUARDIAN_USE_TLS": ("use_tls", lambda v: v.lower() == "true"),
            "GUARDIAN_RECOVERY_LEASE": ("recovery_lease_enabled", lambda v: v.lower() == "true"),
            "GUARDIAN_RECOVERY_LEASE_DIR": ("recovery_lease_dir", str),
            "GUARDIAN_BACKOFF_INITIAL": ("backoff.initial_delay", float),
            "GUARDIAN_BACKOFF_MAX": ("backoff.max_delay", float),
            "GUARDIAN_BACKOFF_MULTIPLIER": ("backoff.multiplier", float),
            "GUARDIAN_BACKOFF_MAX_ATTEMPTS": ("backoff.max_attempts", int),
            "GUARDIAN_BACKOFF_INDEFINITE": ("backoff.indefinite_retry", lambda v: v.lower() == "true"),
        }
        
        for env_var, (attr_path, converter) in env_mapping.items():
            value = os.environ.get(env_var)
            if value is not None:
                try:
                    converted = converter(value)
                    # Handle nested attributes
                    if "." in attr_path:
                        obj, attr = attr_path.split(".", 1)
                        setattr(getattr(config, obj), attr, converted)
                    else:
                        setattr(config, attr_path, converted)
                except Exception as e:
                    print(f"Warning: Failed to parse {env_var}={value}: {e}")
        
        return config
    
    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to file."""
        if path is None:
            path = DEFAULT_CONFIG_PATH
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data = asdict(self)
        # Convert enums to strings
        data["safety_mode"] = self.safety_mode.value
        data["backoff"] = asdict(self.backoff)
        data["backoff"]["jitter_type"] = self.backoff.jitter_type.value
        data["discovery_port_range"] = list(self.discovery_port_range)
        
        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=True)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["safety_mode"] = self.safety_mode.value
        data["backoff"]["jitter_type"] = self.backoff.jitter_type.value
        data["discovery_port_range"] = list(self.discovery_port_range)
        return data


def get_config() -> GuardianConfig:
    """Get the global configuration instance."""
    global _global_config
    if _global_config is None:
        _global_config = GuardianConfig.load()
    return _global_config


def set_config(config: GuardianConfig) -> None:
    """Set the global configuration instance."""
    global _global_config
    _global_config = config


_global_config: Optional[GuardianConfig] = None