"""
Gate.io connector for perpetual futures.

Endpoints used:
- OHLCV:         GET /futures/{settle}/candlesticks
- Funding Rate:  GET /futures/{settle}/funding_rate
- Open Interest: GET /futures/{settle}/contracts/{contract}

Pair format: ETH_USDT (native Gate.io format, no conversion needed).
Timestamps: seconds (native Gate.io format).
"""

import time
import logging
import numpy as np

from .base import BaseConnector
from src.utils.history_manager import append_snapshot_value, get_snapshot_history


logger = logging.getLogger(__name__)

# Gate.io supported kline intervals
TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "8h": "8h",
    "1d": "1d",
    "1w": "7d",
    "7d": "7d",
    "30d": "30d",
}


class GateioConnector(BaseConnector):
    """Gate.io perpetual futures connector."""

    BASE_URL = "https://api.gateio.ws/api/v4"

    def __init__(self):
        super().__init__("gateio")

    def fetch(
        self, scope: str, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        fetchers = {
            "ohlcv": self._fetch_ohlcv,
            "funding_rate": self._fetch_funding_rate,
            "open_interest": self._fetch_open_interest,
            "volatility_compression": self._fetch_volatility_compression,
        }
        fetcher = fetchers.get(scope)
        if fetcher is None:
            logger.error(f"Unsupported scope: {scope}")
            return None
        return fetcher(pair, timeframe, market_type)

    def fetch_history(
        self,
        scope: str,
        pair: str,
        timeframe: str,
        market_type: str,
        lookback: int,
    ) -> list:
        """
        Fetch historical time-series data for Layer 2 analysis.

        Args:
            scope: Data scope ("ohlcv", "funding_rate", "open_interest", "liquidity_density", etc.)
            pair: Trading pair (e.g., "ETH_USDT")
            timeframe: Candlestick timeframe (e.g., "1h")
            market_type: Market type ("perpetual", "spot", etc.)
            lookback: Number of periods to fetch (oldest to newest)

        Returns:
            List of floats: [oldest_value, ..., newest_value]
            Empty list if fetch fails
        """
        fetchers = {
            "ohlcv": self._fetch_ohlcv_history,
            "funding_rate": self._fetch_funding_rate_history,
            "open_interest": self._fetch_open_interest_history,
            "volatility_compression": self._fetch_volatility_compression_history,
        }
        fetcher = fetchers.get(scope)
        if fetcher is None:
            logger.error(f"Unsupported scope for history: {scope}")
            return []
        return fetcher(pair, timeframe, market_type, lookback)

    def _get_settle(self, market_type: str) -> str:
        """Settlement currency based on market type."""
        return "usdt"

    # ----- OHLCV -----

    def _fetch_ohlcv(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        settle = self._get_settle(market_type)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(f"Unsupported timeframe for Gate.io: {timeframe}")
            return None

        from src.utils.constants import TIMEFRAME_SECONDS
        interval_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
        current_time = int(time.time())

        url = f"{self.BASE_URL}/futures/{settle}/candlesticks"
        params = {"contract": pair, "interval": interval, "limit": 2}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        # Filter out unclosed / forming bar: must satisfy timestamp + interval <= current_time
        candle = data[-1]
        if int(candle["t"]) + interval_seconds > current_time:
            if len(data) >= 2:
                candle = data[-2]
            else:
                logger.warning("Latest Gate.io candle is unclosed and no prior candle available")
                return None

        try:
            return {
                "timestamp": int(candle["t"]),
                "open": float(candle["o"]),
                "high": float(candle["h"]),
                "low": float(candle["l"]),
                "close": float(candle["c"]),
                "volume": float(candle["v"]),
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Gate.io OHLCV: {e}")
            return None

    def _fetch_ohlcv_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical close prices from OHLCV candlesticks."""
        settle = self._get_settle(market_type)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(f"Unsupported timeframe for Gate.io: {timeframe}")
            return []

        from src.utils.constants import TIMEFRAME_SECONDS
        interval_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
        current_time = int(time.time())

        url = f"{self.BASE_URL}/futures/{settle}/candlesticks"
        params = {"contract": pair, "interval": interval, "limit": lookback + 1}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) < 1:
            return []

        # Discard unclosed bar if present
        if int(data[-1]["t"]) + interval_seconds > current_time:
            data = data[:-1]
        
        if len(data) == 0:
            logger.warning(f"No OHLCV history returned for {pair}")
            return []

        try:
            closes = []
            for candle in data:
                close_price = float(candle["c"])  # "c" is close
                closes.append(close_price)
            logger.debug(f"Gate.io OHLCV history: {len(closes)} candles")
            return closes
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Gate.io OHLCV history: {e}")
            return []

    # ----- Funding Rate -----

    def _fetch_funding_rate(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        if market_type != "perpetual":
            return None

        settle = self._get_settle(market_type)
        url = f"{self.BASE_URL}/futures/{settle}/funding_rate"
        params = {"contract": pair, "limit": 1}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        entry = data[-1]
        try:
            return {
                "rate": float(entry["r"]),
                "timestamp": int(entry["t"]),
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Gate.io funding rate: {e}")
            return None

    def _fetch_funding_rate_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical funding rates."""
        if market_type != "perpetual":
            return []

        settle = self._get_settle(market_type)
        url = f"{self.BASE_URL}/futures/{settle}/funding_rate"
        # Gate.io API limit is capped at 1000 for funding_rate
        params = {"contract": pair, "limit": min(lookback, 1000)}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            logger.warning(f"No funding rate history returned for {pair}")
            return []

        try:
            rates = []
            for entry in data:
                rate = float(entry["r"])  # "r" is rate
                rates.append(rate)
            logger.debug(f"Gate.io funding rate history: {len(rates)} entries")
            return rates
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Gate.io funding rate history: {e}")
            return []

    # ----- Open Interest -----

    def _fetch_open_interest(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        if market_type != "perpetual":
            return None

        settle = self._get_settle(market_type)
        url = f"{self.BASE_URL}/futures/{settle}/contracts/{pair}"
        data = self._request(url)

        if not data or not isinstance(data, dict):
            return None

        try:
            oi_value = data.get("position_size")
            if oi_value is None:
                logger.warning(
                    "Gate.io contract detail missing position_size"
                )
                return None
            return {
                "value": float(oi_value),
                # Contract endpoint returns current state — use current time
                "timestamp": int(time.time()),
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Gate.io open interest: {e}")
            return None

    def _fetch_open_interest_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """
        Fetch historical open interest.
        
        Gate.io REST API only provides current snapshot.
        This method accumulates snapshots across runs using persistent storage.
        Returns accumulated historical data (or current if < 2 points).
        """
        if market_type != "perpetual":
            return []

        settle = self._get_settle(market_type)
        url = f"{self.BASE_URL}/futures/{settle}/contracts/{pair}"
        data = self._request(url)

        if not data or not isinstance(data, dict):
            logger.warning(f"No open interest data returned for {pair}")
            # Return accumulated history if available
            return get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )

        try:
            oi_value = data.get("position_size")
            if oi_value is None:
                logger.warning(
                    "Gate.io contract detail missing position_size"
                )
                return get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )
            
            oi_float = float(oi_value)
            
            # Append current snapshot to persistent history
            append_snapshot_value(
                "open_interest", oi_float, pair, timeframe,
                exchange=self.exchange_id,
            )
            
            # Load and return accumulated history
            history = get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )
            logger.debug(
                f"Open interest: current={oi_float:.2f}, "
                f"accumulated_points={len(history)}"
            )
            return history if history else [oi_float]
            
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse Gate.io open interest: {e}")
            return get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )



    # ----- Volatility Compression -----

    def _fetch_volatility_compression(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        """Fetch recent volatility measurement (standard deviation of returns)."""
        settle = self._get_settle(market_type)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            return None

        url = f"{self.BASE_URL}/futures/{settle}/candlesticks"
        params = {"contract": pair, "interval": interval, "limit": 20}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) < 2:
            return None

        try:
            closes = [float(c["c"]) for c in data]
            
            # Calculate log returns
            log_prices = np.log(closes)
            returns = list(np.diff(log_prices))
            
            # Calculate volatility as standard deviation * 100 for percentage
            if len(returns) == 0:
                return None
            
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            volatility_percent = (variance ** 0.5) * 100
            
            return {
                "volatility_percent": volatility_percent,
                "timestamp": int(time.time()),
            }
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning(f"Failed to parse Gate.io volatility compression: {e}")
            return None

    def _fetch_volatility_compression_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical volatility compression."""
        settle = self._get_settle(market_type)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            return []

        url = f"{self.BASE_URL}/futures/{settle}/candlesticks"
        # Gate.io API limit is capped at 2000 for candlesticks
        params = {"contract": pair, "interval": interval, "limit": min(lookback + 1, 2000)}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) < 2:
            return []

        try:
            volatilities = []
            for i in range(20, len(data)):
                # Rolling window of 20 candles
                window = [float(c["c"]) for c in data[i-20:i+1]]
                log_window = np.log(window)
                returns = list(np.diff(log_window))
                
                if len(returns) > 0:
                    mean_ret = sum(returns) / len(returns)
                    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                    vol = (variance ** 0.5) * 100
                    volatilities.append(vol)
            
            logger.debug(f"Gate.io volatility compression history: {len(volatilities)} points")
            return volatilities
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as e:
            logger.error(f"Failed to parse Gate.io volatility history: {e}")
            return []



    # ----- Validation -----

    def validate_pair(self, pair: str, market_type: str) -> bool:
        settle = self._get_settle(market_type)
        url = f"{self.BASE_URL}/futures/{settle}/contracts/{pair}"
        data = self._request(url)
        return (
            data is not None
            and isinstance(data, dict)
            and "name" in data
        )

    def validate_timeframe(self, timeframe: str) -> bool:
        return timeframe in TIMEFRAME_MAP

    def get_supported_timeframes(self) -> list:
        return list(TIMEFRAME_MAP.keys())
    def get_leverage_limit(self, pair: str, market_type: str) -> float | None:
        """
        Fetch maximum leverage limit for a trading pair from Gate.io API.
        
        Calls /futures/{settle}/contracts/{contract} to get leverage restrictions.
        Returns the maximum allowed leverage for perpetual futures trading.
        """
        settle = self._get_settle(market_type)
        if settle is None:
            logger.warning(f"Invalid market type for leverage query: {market_type}")
            return None
        
        url = f"{self.BASE_URL}/futures/{settle}/contracts/{pair}"
        
        try:
            data = self._request(url)
            if data is None or not isinstance(data, dict):
                logger.error(f"Failed to fetch contract info for {pair} from Gate.io")
                return None
            
            # Get leverage_max from contract data (API returns as string "200")
            if "leverage_max" in data:
                try:
                    leverage = float(data["leverage_max"])
                    logger.info(f"Gate.io leverage limit for {pair}: {leverage}x (from API)")
                    return leverage
                except (ValueError, TypeError) as e:
                    logger.error(f"Failed to parse leverage_max value '{data['leverage_max']}': {e}")
                    return None
            
            # If API does not provide leverage_max, return None
            # No human defaults, no fallbacks — only exchange data
            logger.error(f"Gate.io contract info for {pair} does not include leverage_max field")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching leverage limit for {pair} from Gate.io: {e}")
            return None

