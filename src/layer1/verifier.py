"""
Layer 1 — The Existence Oracle

Tugas: Verifikasi apakah data dari suatu sumber benar-benar ada.
Output: Biner 0/1 per scope per interval waktu.

ATURAN (dari Blueprint):
- Tidak ada interpretasi makna
- Tidak ada perbandingan threshold
- Tidak ada filter konten
- Hanya: ada atau tidak

EKSTENSI (dari Blueprint Supplement):
- Structural sanity checks BOLEH: format-based, bukan meaning-based
- Parseable type, timestamp freshness, mathematically possible range
- Field wajib per scope harus lengkap
"""

import time
import logging
from typing import Any

from src.utils.config_loader import get_sources_config, get_infrastructure_config
from src.utils.constants import TIMEFRAME_SECONDS


logger = logging.getLogger(__name__)

# Structural validation rules per scope
# These are FORMAT checks, not MEANING checks
# volume < 0 is mathematically impossible (definition, not judgment)
# high < low is impossible by definition of OHLCV
STRUCTURAL_RULES: dict[str, dict[str, Any]] = {
    "ohlcv": {
        "non_negative": ["volume"],
        "strictly_positive": ["open", "high", "low", "close"],
        "high_gte_low": True,
    },
    "funding_rate": {
        # Rate can be negative — that is valid data, not broken data
    },
    "open_interest": {
        "non_negative": ["value"],
    },
}


class Verifier:
    """
    Layer 1 existence verifier.

    For each configured scope:
    1. Fetch data via connector
    2. Raw existence check (None, empty → 0)
    3. Structural sanity check (required fields, parseability, freshness, rules)
    4. Output: 0 or 1

    No meaning is interpreted. No thresholds. No comparisons.
    """

    def __init__(self):
        self.sources_config = get_sources_config()
        infra_config = get_infrastructure_config()
        self.max_staleness = infra_config["timestamp"]["max_staleness_intervals"]
        self.scopes = self.sources_config["scopes"]

    def verify_all(
        self, connector, pair: str, timeframe: str, market_type: str
    ) -> dict:
        """
        Run existence check on all configured scopes.

        Returns:
            dict: {scope_name: 0 or 1} for each scope
        """
        results = {}

        for scope_name in self.scopes:
            data = connector.fetch(scope_name, pair, timeframe, market_type)
            result = self._check_existence(scope_name, data, timeframe)
            results[scope_name] = result

            status = "EXISTS (1)" if result == 1 else "ABSENT (0)"
            logger.info(f"Layer 1 | {scope_name}: {status}")

        # Log summary
        active = sum(results.values())
        total = len(results)
        logger.info(
            f"Layer 1 complete | {active}/{total} scopes active"
        )

        return results

    def get_active_scopes(self, results: dict) -> list:
        """Return list of scope names that passed existence check."""
        return [name for name, value in results.items() if value == 1]

    def get_scope_count(self, results: dict) -> int:
        """Return count of active scopes (for Layer 2 metadata)."""
        return sum(results.values())

    # ----- Core existence check -----

    def _check_existence(
        self, scope_name: str, data, timeframe: str
    ) -> int:
        """
        The Layer 1 function from the blueprint, extended with
        structural sanity checks from the supplement.

        Returns 0 or 1. Nothing else. Ever.
        """
        # Step 1: Raw existence (directly from blueprint)
        if data is None:
            return 0
        if data == "":
            return 0
        if data == []:
            return 0
        if isinstance(data, dict) and len(data) == 0:
            return 0

        # Step 2: Structural sanity checks (format-based only)
        if not self._check_required_fields(scope_name, data):
            return 0
        if not self._check_parseability(scope_name, data):
            return 0
        if not self._check_timestamp_freshness(scope_name, data, timeframe):
            return 0
        if not self._check_structural_rules(scope_name, data):
            return 0

        # Data exists and is structurally sound
        return 1

    # ----- Structural checks -----

    def _check_required_fields(self, scope_name: str, data: dict) -> bool:
        """All required fields must be present and non-null."""
        required = self.scopes[scope_name]["required_fields"]
        for field in required:
            if field not in data:
                logger.warning(
                    f"Structural check FAILED: missing field "
                    f"'{field}' in {scope_name}"
                )
                return False
            if data[field] is None:
                logger.warning(
                    f"Structural check FAILED: null value for "
                    f"'{field}' in {scope_name}"
                )
                return False
        return True

    def _check_parseability(self, scope_name: str, data: dict) -> bool:
        """All required fields must be parseable as numeric values."""
        required = self.scopes[scope_name]["required_fields"]
        for field in required:
            value = data[field]
            if not isinstance(value, (int, float)):
                try:
                    float(value)
                except (ValueError, TypeError):
                    logger.warning(
                        f"Structural check FAILED: '{field}' not "
                        f"parseable as numeric in {scope_name}: {value}"
                    )
                    return False
        return True

    def _check_timestamp_freshness(
        self, scope_name: str, data: dict, timeframe: str
    ) -> bool:
        """
        Timestamp must be within max_staleness_intervals of the scope's
        own update frequency.

        update_interval_seconds comes from config/sources.yaml:
        - "dynamic" = follows user's timeframe (e.g. OHLCV, OI)
        - fixed int = from exchange API spec (e.g. funding_rate = 28800s/8h)

        This is a structural check based on technical facts from the
        exchange API spec, not a meaning check or human judgment.
        """
        if "timestamp" not in data:
            return True

        timestamp = data["timestamp"]

        # Determine update interval for this scope
        scope_config = self.scopes[scope_name]
        update_interval = scope_config.get("update_interval_seconds", "dynamic")
        if update_interval == "dynamic":
            interval_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
        else:
            interval_seconds = int(update_interval)

        max_age = self.max_staleness * interval_seconds
        current_time = int(time.time())
        age = current_time - timestamp

        if age > max_age:
            logger.warning(
                f"Structural check FAILED: {scope_name} data too old | "
                f"age={age}s, max_allowed={max_age}s "
                f"({self.max_staleness} x {interval_seconds}s)"
            )
            return False

        # Timestamp significantly in the future is also suspicious
        if age < -interval_seconds:
            logger.warning(
                f"Structural check FAILED: {scope_name} timestamp in "
                f"the future | age={age}s"
            )
            return False

        return True

    def _check_structural_rules(self, scope_name: str, data: dict) -> bool:
        """
        Apply scope-specific structural rules.

        These are DEFINITION checks, not JUDGMENT checks:
        - Volume cannot be negative (mathematical impossibility)
        - High cannot be less than Low (OHLCV definition)
        """
        rules = STRUCTURAL_RULES.get(scope_name, {})

        # Non-negative check
        non_neg_fields: list[str] = rules.get("non_negative", [])
        for field in non_neg_fields:
            if field in data:
                value = data[field]
                if not isinstance(value, (int, float)):
                    value = float(value)
                if value < 0:
                    logger.warning(
                        f"Structural check FAILED: {scope_name}.{field} "
                        f"= {value} (negative is mathematically impossible)"
                    )
                    return False

        # Strictly positive check (e.g. prices must be > 0)
        strictly_positive_fields: list[str] = rules.get("strictly_positive", [])
        for field in strictly_positive_fields:
            if field in data:
                value = data[field]
                if not isinstance(value, (int, float)):
                    value = float(value)
                if value <= 0:
                    logger.warning(
                        f"Structural check FAILED: {scope_name}.{field} "
                        f"= {value} (price <= 0 is invalid)"
                    )
                    return False

        # High >= Low check (OHLCV definition)
        check_high_low: bool = rules.get("high_gte_low", False)
        if check_high_low:
            if "high" in data and "low" in data:
                high = data["high"]
                low = data["low"]
                if not isinstance(high, (int, float)):
                    high = float(high)
                if not isinstance(low, (int, float)):
                    low = float(low)
                if high < low:
                    logger.warning(
                        f"Structural check FAILED: OHLCV high ({high}) "
                        f"< low ({low}) (impossible by definition)"
                    )
                    return False

        return True
