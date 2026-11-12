"""
Config loader utility.
Loads YAML configuration files from the config/ directory.
"""

from pathlib import Path

import yaml


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


def load_config(filename: str) -> dict:
    """Load a YAML config file from the config/ directory."""
    filepath = _CONFIG_DIR / filename
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_sources_config() -> dict:
    """Load sources.yaml — scope definitions and platform list."""
    return load_config("sources.yaml")


def get_infrastructure_config() -> dict:
    """Load infrastructure.yaml — retry, timeout, staleness parameters."""
    return load_config("infrastructure.yaml")


def get_execution_scopes_config() -> dict:
    """Load execution_scopes.yaml — Layer 3 Kategori B scope definitions.

    Returns scope definitions including min_signals thresholds and
    descriptions for sl_quality, tp_quality, leverage_quality,
    and signal_confidence evaluators.
    """
    return load_config("execution_scopes.yaml")


def get_scope_min_signals(scope_name: str) -> int:
    """Get minimum resolved signals required before a Kategori B scope activates.

    Args:
        scope_name: One of 'sl_quality', 'tp_quality',
                    'leverage_quality', 'signal_confidence'

    Returns:
        Minimum signal count (default 50 if not found in config).
    """
    config = get_execution_scopes_config()
    scopes = config.get("scopes", {})
    scope = scopes.get(scope_name, {})
    return int(scope.get("min_signals", 50))
