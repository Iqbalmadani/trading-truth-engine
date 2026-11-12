"""
Binance connector for perpetual futures (USDⓈ-M).

Endpoints used:
- OHLCV:         GET /fapi/v1/klines
- Funding Rate:  GET /fapi/v1/fundingRate
- Open Interest: GET /fapi/v1/openInterest

Pair format: Converts ETH_USDT → ETHUSDT (Binance native format).
Timestamps: Binance uses milliseconds, normalized to seconds in output.
"""

import logging
import time
import numpy as np

from .base import BaseConnector
from src.utils.history_manager import append_snapshot_value, get_snapshot_history


logger = logging.getLogger(__name__)

# Binance USDⓈ-M futures supported kline intervals
TIMEFRAME_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "3d": "3d",
    "1w": "1w",
}


class BinanceConnector(BaseConnector):
    """Binance USDⓈ-M perpetual futures connector."""

    BASE_URL = "https://fapi.binance.com"

    def __init__(self):
        super().__init__("binance")

    @staticmethod
    def _to_binance_symbol(pair: str) -> str:
        """Convert ETH_USDT → ETHUSDT."""
        return pair.replace("_", "")

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

    # ----- OHLCV -----

    def _fetch_ohlcv(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        symbol = self._to_binance_symbol(pair)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(
                f"Unsupported timeframe for Binance: {timeframe}"
            )
            return None

        url = f"{self.BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": 1}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        candle = data[-1]
        try:
            # Binance kline: [open_time, open, high, low, close, volume, ...]
            return {
                "timestamp": int(candle[0]) // 1000,  # ms → seconds
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            }
        except (IndexError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Binance OHLCV: {e}")
            return None

    def _fetch_ohlcv_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical close prices from OHLCV klines."""
        symbol = self._to_binance_symbol(pair)
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(
                f"Unsupported timeframe for Binance: {timeframe}"
            )
            return []

        url = f"{self.BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": lookback}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            logger.warning(f"No OHLCV history returned for {pair}")
            return []

        try:
            closes = []
            for candle in data:
                close_price = float(candle[4])  # close is at index 4
                closes.append(close_price)
            logger.debug(f"Binance OHLCV history: {len(closes)} candles")
            return closes
        except (IndexError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Binance OHLCV history: {e}")
            return []

    # ----- Funding Rate -----

    def _fetch_funding_rate(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        if market_type != "perpetual":
            return None

        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": 1}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        entry = data[-1]
        try:
            return {
                "rate": float(entry["fundingRate"]),
                "timestamp": int(entry["fundingTime"]) // 1000,  # ms → s
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Binance funding rate: {e}")
            return None

    def _fetch_funding_rate_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical funding rates."""
        if market_type != "perpetual":
            return []

        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": lookback}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) == 0:
            logger.warning(f"No funding rate history returned for {pair}")
            return []

        try:
            rates = []
            for entry in data:
                rate = float(entry["fundingRate"])
                rates.append(rate)
            logger.debug(f"Binance funding rate history: {len(rates)} entries")
            return rates
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Binance funding rate history: {e}")
            return []

    # ----- Open Interest -----

    def _fetch_open_interest(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        if market_type != "perpetual":
            return None

        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/openInterest"
        params = {"symbol": symbol}
        data = self._request(url, params)

        if not data or not isinstance(data, dict):
            return None

        try:
            return {
                "value": float(data["openInterest"]),
                "timestamp": int(data["time"]) // 1000,  # ms → seconds
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Binance open interest: {e}")
            return None

    def _fetch_open_interest_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """
        Fetch open interest history for Layer 2.

        Binance REST only exposes current OI; snapshots are accumulated per
        namespace (exchange/pair/timeframe) across LIVE/calibration runs.
        """
        if market_type != "perpetual":
            return []

        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/openInterest"
        params = {"symbol": symbol}
        data = self._request(url, params)

        if not data or not isinstance(data, dict):
            logger.warning(f"No open interest data returned for {pair}")
            return get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )

        try:
            oi_value = float(data["openInterest"])
            append_snapshot_value(
                "open_interest", oi_value, pair, timeframe,
                exchange=self.exchange_id,
            )
            history = get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )
            logger.debug(
                f"Binance open interest: current={oi_value:.2f}, "
                f"accumulated_points={len(history)}"
            )
            return history if history else [oi_value]
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse Binance open interest: {e}")
            return get_snapshot_history(
                "open_interest", pair=pair, timeframe=timeframe,
                exchange=self.exchange_id,
            )



    # ----- Volatility Compression -----

    def _fetch_volatility_compression(
        self, pair: str, timeframe: str, market_type: str
    ) -> dict | None:
        """Fetch recent volatility measurement (standard deviation of returns)."""
        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/klines"
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(f"Unsupported timeframe for Binance: {timeframe}")
            return None
        params = {"symbol": symbol, "interval": interval, "limit": 20}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) < 2:
            return None

        try:
            closes = [float(candle[4]) for candle in data]
            
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
                "timestamp": int(data[-1][0]) // 1000,  # ms → seconds
            }
        except (KeyError, ValueError, TypeError, ZeroDivisionError, IndexError) as e:
            logger.warning(f"Failed to parse Binance volatility compression: {e}")
            return None

    def _fetch_volatility_compression_history(
        self, pair: str, timeframe: str, market_type: str, lookback: int
    ) -> list:
        """Fetch historical volatility compression."""
        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/klines"
        interval = TIMEFRAME_MAP.get(timeframe)
        if interval is None:
            logger.warning(f"Unsupported timeframe for Binance: {timeframe}")
            return []
        params = {"symbol": symbol, "interval": interval, "limit": lookback + 1}
        data = self._request(url, params)

        if not data or not isinstance(data, list) or len(data) < 2:
            return []

        try:
            volatilities = []
            for i in range(20, len(data)):
                # Rolling window of 20 candles
                window = [float(candle[4]) for candle in data[i-20:i+1]]
                log_window = np.log(window)
                returns = list(np.diff(log_window))
                
                if len(returns) > 0:
                    mean_ret = sum(returns) / len(returns)
                    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                    vol = (variance ** 0.5) * 100
                    volatilities.append(vol)
            
            logger.debug(f"Binance volatility compression history: {len(volatilities)} points")
            return volatilities
        except (KeyError, ValueError, TypeError, ZeroDivisionError, IndexError) as e:
            logger.error(f"Failed to parse Binance volatility history: {e}")
            return []



    # ----- Validation -----

    def validate_pair(self, pair: str, market_type: str) -> bool:
        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/ticker/price"
        params = {"symbol": symbol}
        data = self._request(url, params)
        return (
            data is not None
            and isinstance(data, dict)
            and "symbol" in data
        )

    def validate_timeframe(self, timeframe: str) -> bool:
        return timeframe in TIMEFRAME_MAP

    def get_supported_timeframes(self) -> list:
        return list(TIMEFRAME_MAP.keys())

    def get_leverage_limit(self, pair: str, market_type: str) -> float | None:
        """
        Fetch maximum leverage limit for a trading pair from exchange API.
        """
        if market_type != "perpetual":
            logger.warning(f"get_leverage_limit only supports perpetual market, got {market_type}")
            return None

        symbol = self._to_binance_symbol(pair)
        url = f"{self.BASE_URL}/fapi/v1/exchangeInfo"
        
        try:
            data = self._request(url)
            if data is None or not isinstance(data, dict) or "symbols" not in data:
                logger.error("Failed to fetch exchange info from Binance")
                return None
            
            # Find symbol in exchange info
            for sym_data in data["symbols"]:
                if sym_data.get("symbol") == symbol:
                    if "maxLeverage" in sym_data:
                        leverage = float(sym_data["maxLeverage"])
                        logger.info(f"Binance leverage limit for {pair}: {leverage}x (from exchangeInfo)")
                        return leverage
                    
                    # Fallback: query leverageBrackets endpoint
                    return self._fetch_leverage_brackets(symbol)
            
            logger.warning(f"Symbol {symbol} not found in Binance exchange info")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching leverage limit for {pair}: {e}")
            return None

    def _fetch_leverage_brackets(self, symbol: str) -> float | None:
        """
        Fetch leverage brackets for a specific symbol.
        Returns the maximum leverage from the brackets.
        """
        url = f"{self.BASE_URL}/fapi/v1/leverageBracket"
        params = {"symbol": symbol}
        
        try:
            data = self._request(url, params)
            if data is None or not isinstance(data, list) or len(data) == 0:
                logger.error(f"Failed to fetch leverage brackets for {symbol}")
                return None
            
            # Get maximum leverage from brackets
            brackets = data[0].get("brackets", [])
            if brackets:
                max_leverage = float(brackets[-1].get("initialLeverage", 1.0))
                logger.info(f"Binance leverage brackets for {symbol}: {max_leverage}x")
                return max_leverage
            
            logger.warning(f"No leverage brackets found for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching leverage brackets for {symbol}: {e}")
            return None