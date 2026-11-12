"""
Central registry for exchange connectors.

Adding a new exchange:
  1. Subclass BaseConnector with exchange_id in __init__
  2. Register here
  3. Add to config/trading_pairs.yaml and config/sources.yaml platforms
"""

from .base import BaseConnector
from .binance import BinanceConnector
from .gateio import GateioConnector

CONNECTORS: dict[str, type[BaseConnector]] = {
    "gateio": GateioConnector,
    "binance": BinanceConnector,
}


def get_connector(exchange: str) -> BaseConnector:
    """Instantiate a connector for the given exchange id."""
    key = str(exchange).strip().lower()
    connector_class = CONNECTORS.get(key)
    if connector_class is None:
        registered = ", ".join(sorted(CONNECTORS))
        raise ValueError(
            f"Unknown exchange '{exchange}'. Registered: {registered}"
        )
    return connector_class()
