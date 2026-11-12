"""
Base connector — abstract interface for all exchange connectors.

Provides:
- Retry logic (max_attempts from config/infrastructure.yaml)
- HTTP request handling with timeout
- Abstract methods that each exchange connector must implement

Adding a new exchange = subclass this, implement the abstract methods.
No changes to Layer 1 logic required.
"""

import time
import logging
from abc import ABC, abstractmethod

import requests

from src.utils.config_loader import get_infrastructure_config


logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """Abstract base class for exchange API connectors."""

    def __init__(self, exchange_id: str):
        if not exchange_id or not str(exchange_id).strip():
            raise ValueError(
                "exchange_id is required for SQLite namespace isolation"
            )
        self.exchange_id = str(exchange_id).strip().lower()
        infra = get_infrastructure_config()
        self.max_retries = infra["retry"]["max_attempts"]
        self.retry_delay = infra["retry"]["delay_seconds"]
        self.timeout = infra["request"]["timeout_seconds"]

    def _request(self, url: str, params: dict | None = None) -> dict | list | None:
        """
        Make an HTTP GET request with retry logic.

        Retry count comes from config/infrastructure.yaml — not hardcoded.
        Returns parsed JSON on success, None after all retries exhausted.
        """
        for attempt in range(1, self.max_retries + 1):
            response = None
            try:
                response = requests.get(
                    url, params=params, timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                # 4xx errors (e.g. invalid symbol) — don't retry
                if response is not None and response.status_code < 500:
                    logger.warning(
                        f"Client error for {url}: {response.status_code} {e}"
                    )
                    return None
                logger.warning(
                    f"Attempt {attempt}/{self.max_retries} server error "
                    f"for {url}: {e}"
                )
            except (requests.RequestException, ValueError) as e:
                logger.warning(
                    f"Attempt {attempt}/{self.max_retries} failed "
                    f"for {url}: {e}"
                )

            if attempt < self.max_retries:
                time.sleep(self.retry_delay)

        logger.error(f"All {self.max_retries} attempts failed for {url}")
        return None

    @abstractmethod
    def fetch(
        self, scope: str, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        """
        Fetch data for a specific scope.

        Returns a normalized dict with the scope's required fields,
        or None if data is unavailable.
        """
        ...

    @abstractmethod
    def validate_pair(self, pair: str, market_type: str) -> bool:
        """Check if a trading pair exists on this exchange."""
        ...

    @abstractmethod
    def validate_timeframe(self, timeframe: str) -> bool:
        """Check if a timeframe is supported by this exchange."""
        ...

    @abstractmethod
    def get_supported_timeframes(self) -> list:
        """Return list of supported timeframe strings."""
        ...
    @abstractmethod
    def get_leverage_limit(self, pair: str, market_type: str) -> float | None:
        """
        Fetch maximum leverage limit for a trading pair from exchange API.
        
        Args:
            pair: Trading pair (e.g., "ETH_USDT")
            market_type: Market type (e.g., "perpetual")
            
        Returns:
            Maximum leverage as float (e.g., 20.0) or None if unavailable.
            
        Must fetch from exchange API at runtime — no hardcoded values.
        If API call fails after retries, return None and signal refusal to trade.
        """
        ...