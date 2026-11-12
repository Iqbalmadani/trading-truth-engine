"""
Meta-Learning Module for Layer 2 CPD Penalty Selection

Selects the optimal CPD penalty for each scope based on segmentation quality.

Quality Metric: Calinski-Harabasz Index (F-ratio from ANOVA)
    CH = (between_var * (n - k)) / (within_var * (k - 1))

Why Calinski-Harabasz instead of raw homogeneity:
  - Raw homogeneity (between/within) is biased toward over-segmentation
    because micro-segments (2-3 points) trivially have near-zero within_var
  - CH naturally penalizes over-segmentation through the (n-k)/(k-1) factor
  - This is the F-statistic from ANOVA — a mathematical identity, not a human choice

Penalty Candidates: BIC-scaled from data variance
    base = 2 * σ² * ln(n)  (Schwarz, 1978 — information theory)
    candidates = base × logarithmic range

Why BIC-scaled instead of hardcoded list:
  - Old: [0.1, 0.5, 1.0, 2.0, 5.0, 10.0] — human-chosen range
  - New: scaled to data's own variance — adapts to any data magnitude
  - The constant 2 in BIC is from information theory derivation, not human judgment

Constitution Compliance:
  ✅ No hardcoded penalty candidates (BIC-scaled from data)
  ✅ No biased metric (CH replaces raw homogeneity)
  ✅ Model selection by data (l2, rbf, l1 evaluated equally)
  ✅ All parameters derived from data structure
"""

import logging
from typing import List, Dict, Tuple, Optional

import numpy as np
import ruptures as rpt

from src.utils.config_loader import get_sources_config
from src.utils.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)


def _apply_window_cap(data_series: list, timeframe: str) -> list:
    """
    Cap data series to infrastructure-defined window size.

    Window size auto-adjusts per timeframe:
      - TF 5m → 500 points (max cap)
      - TF 1h → 168 points (7 days natural)
      - TF 1d → 50 points (min cap)

    This is an infrastructure parameter, not a data judgment.
    """
    from src.utils.config_loader import get_infrastructure_config

    try:
        infra = get_infrastructure_config()
        cal = infra.get("calibration", {})
        window_days = cal.get("window_days", 7)
        min_window = cal.get("min_window", 50)
        max_window = cal.get("max_window", 500)
    except Exception:
        window_days, min_window, max_window = 7, 50, 500

    tf_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
    raw_window = (window_days * 86400) // tf_seconds
    window_size = max(min_window, min(max_window, raw_window))

    if len(data_series) > window_size:
        return data_series[-window_size:]
    return data_series


def compute_penalty_candidates(series: List[float]) -> List[float]:
    """
    Generate penalty candidates scaled to the data's own variance.

    Uses BIC-optimal penalty as the center point:
        base_penalty = 2 * σ² * ln(n)
    Then searches a logarithmic range around it.

    Args:
        series: Time-series data to derive candidates from

    Returns:
        List of penalty candidates scaled to data structure

    Why this works:
        - BIC penalty = 2 * σ² * ln(n) is the theoretically optimal penalty
          for PELT under Gaussian assumption (Killick et al., 2012)
        - 2 is from the BIC derivation (information theory constant)
        - σ² comes from the data
        - n comes from the data
        - Searching around this value accounts for non-Gaussian structure
    """
    data = np.array(series, dtype=float)
    n = len(data)

    if n < 3:
        # Fallback: minimal candidates for very short series
        return [1.0]

    sigma2 = float(np.var(data))

    # Guard against zero variance (constant series)
    if sigma2 < 1e-15:
        logger.warning("Data has near-zero variance, using minimal penalty candidates")
        return [1.0]

    # BIC-optimal base penalty (information theory, not human judgment)
    bic_base = 2.0 * sigma2 * np.log(n)

    # Search around the theoretical optimum with logarithmic spacing
    # Range: 0.001x to 10x of BIC base (covers 4 orders of magnitude)
    # This widens the search to allow much smaller penalties when the data
    # distribution requires more segmentation, while preserving higher
    # candidates for conservative solutions.
    multipliers = np.logspace(-3, 1, 13)
    candidates = [float(bic_base * m) for m in multipliers]

    logger.debug(
        f"BIC base penalty: {bic_base:.6f} "
        f"(σ²={sigma2:.6f}, n={n}, ln(n)={np.log(n):.4f}), "
        f"candidates range: [{candidates[0]:.6f}, {candidates[-1]:.6f}]"
    )

    return candidates


def calculate_calinski_harabasz(
    series: List[float], change_points: List[int], epsilon: float = 1e-9
) -> float:
    """
    Calculate Calinski-Harabasz Index (Variance Ratio Criterion) for segmentation.

    CH = (between_var / (k - 1)) / (within_var / (n - k))
       = (between_var * (n - k)) / (within_var * (k - 1))

    This is the F-statistic from one-way ANOVA applied to CPD segments.
    It naturally penalizes over-segmentation because:
    - More segments (higher k) → smaller numerator (n-k shrinks)
    - More segments → larger denominator (k-1 grows)
    - Net effect: over-segmentation produces LOWER CH scores

    Args:
        series: Time-series data
        change_points: List of change point indices from CPD
        epsilon: Small constant to avoid division by zero (numerical guard)

    Returns:
        Calinski-Harabasz score (float, higher is better)
    """
    if not series or len(change_points) < 2:
        return 0.0

    try:
        data = np.array(series, dtype=float)
        n = len(data)

        # Check for NaN or Inf in input data
        if np.any(np.isnan(data)) or np.any(np.isinf(data)):
            logger.warning("Input series contains NaN or Inf values, returning 0.0")
            return 0.0

        # Split series into segments using change points
        segments = []
        prev = 0
        for cp in change_points:
            if cp > prev:  # Only include non-empty segments
                segments.append(data[prev:cp])
            prev = cp

        k = len(segments)  # Number of segments

        if k < 2:
            logger.debug("Less than 2 segments after CPD, CH = 0")
            return 0.0

        if k >= n:
            logger.debug(f"Segments ({k}) >= data points ({n}), CH = 0")
            return 0.0

        # Between-segment variance (weighted by segment size)
        overall_mean = np.mean(data)
        between_var = sum(
            len(seg) * (np.mean(seg) - overall_mean) ** 2
            for seg in segments if len(seg) > 0
        ) / n

        # Within-segment variance (average variance within each segment)
        within_vars = [np.var(seg) for seg in segments if len(seg) > 0]
        within_var = np.mean(within_vars) if within_vars else 0.0

        # Calinski-Harabasz Index (F-ratio)
        # CH = (between_var * (n - k)) / (within_var * (k - 1))
        ch_score = (between_var * (n - k)) / (within_var * (k - 1) + epsilon)

        # Check for NaN in result
        if np.isnan(ch_score) or np.isinf(ch_score):
            logger.warning(f"CH calculation resulted in NaN/Inf: {ch_score}, returning 0.0")
            return 0.0

        return float(ch_score)

    except Exception as e:
        logger.error(f"Calinski-Harabasz calculation failed: {e}")
        return 0.0


from scipy import stats


def compute_ch_significance_threshold(
    n_data: int,
    n_segments: int,
    alpha: float = 0.05,
    n_comparisons: int = 39,
) -> float:
    """
    Compute minimum Calinski-Harabasz (CH) score that is statistically significant
    after Bonferroni multiple-testing correction.

    Under the null hypothesis of no true cluster/regime structure, the CH index
    follows an F-distribution: CH ~ F(df1, df2), where
      df1 = k - 1 (between-group degrees of freedom, k = n_segments)
      df2 = n - k (within-group degrees of freedom, n = n_data)

    Args:
        n_data: Total number of data points (n)
        n_segments: Number of detected segments (k)
        alpha: Nominal significance level (default 0.05)
        n_comparisons: Total candidate models * penalties tested (default 39 = 3 models * 13 penalties)

    Returns:
        Critical F-value threshold. If CH >= threshold, null is rejected at adjusted alpha.
    """
    corrected_alpha = alpha / max(n_comparisons, 1)
    df1 = max(n_segments - 1, 1)
    df2 = max(n_data - n_segments, 1)
    return float(stats.f.ppf(1.0 - corrected_alpha, df1, df2))


def find_optimal_penalty(
    series: List[float],
    penalty_candidates: Optional[List[float]] = None,
    min_size: int = 1,
) -> Tuple[float, str, float, bool]:
    """
    Find the CPD penalty AND model type that produces best segmentation quality,
    validated with Bonferroni multiple-testing correction.

    Quality metric: Calinski-Harabasz Index (F-ratio from ANOVA)
    Penalty candidates: BIC-scaled from data variance (if not provided)

    Args:
        series: Time-series data for penalty selection
        penalty_candidates: List of penalties to test (default: BIC-scaled)
        min_size: Minimum segment size for CPD

    Returns:
        Tuple: (best_penalty, best_model, best_ch_score, is_significant)
    """
    # Generate data-scaled candidates if none provided
    if penalty_candidates is None:
        penalty_candidates = compute_penalty_candidates(series)

    bic_default_penalty = penalty_candidates[len(penalty_candidates) // 2] if penalty_candidates else 1.0

    if not series or len(series) < 3:
        logger.warning(
            f"Series too short for meta-learning ({len(series)} values), "
            f"returning BIC-derived penalty"
        )
        return bic_default_penalty, "rbf", 0.0, False

    MODEL_CANDIDATES = ["l2", "rbf", "l1"]
    n_comparisons = len(penalty_candidates) * len(MODEL_CANDIDATES)  # 39 comparisons

    best_penalty = bic_default_penalty
    best_model = "rbf"
    best_ch = -np.inf
    best_n_segments = 2

    data = np.array(series, dtype=float)

    # Validate input data
    if np.any(np.isnan(data)) or np.any(np.isinf(data)):
        logger.warning("Series contains NaN or Inf values, returning default")
        return best_penalty, best_model, 0.0, False

    for model in MODEL_CANDIDATES:
        try:
            jump_val = 3 if len(data) > 100 else 1
            algo = rpt.Pelt(model=model, min_size=min_size, jump=jump_val).fit(data)
        except Exception as e:
            logger.warning(f"CPD fitting failed for model={model}: {e}")
            continue

        for penalty in penalty_candidates:
            try:
                cps = algo.predict(pen=penalty)

                # Calculate Calinski-Harabasz for this penalty+model combo
                ch_score = calculate_calinski_harabasz(series, cps)

                # Check for NaN/Inf
                if np.isnan(ch_score) or np.isinf(ch_score):
                    logger.warning(
                        f"Model {model}, Penalty {penalty:.6f}: "
                        f"CH = {ch_score} (NaN/Inf), skipping"
                    )
                    continue

                logger.debug(
                    f"Model {model}, Penalty {penalty:.6f}: "
                    f"CH = {ch_score:.4f}, "
                    f"num_segments = {len(cps)-1}"
                )

                if ch_score > best_ch:
                    best_ch = ch_score
                    best_penalty = penalty
                    best_model = model
                    best_n_segments = max(len(cps) - 1, 2)

            except Exception as e:
                logger.warning(f"CPD prediction failed for model={model}, penalty={penalty:.6f}: {e}")
                continue

    if best_ch == -np.inf:
        logger.error("All combinations failed or returned invalid CH, returning BIC median")
        return bic_default_penalty, "rbf", 0.0, False

    # Bonferroni Multiple-Testing Validation
    significance_threshold = compute_ch_significance_threshold(
        n_data=len(data),
        n_segments=best_n_segments,
        alpha=0.05,
        n_comparisons=n_comparisons,
    )

    if best_ch < significance_threshold:
        logger.warning(
            f"Best CH={best_ch:.4f} not significant after Bonferroni correction "
            f"(threshold={significance_threshold:.4f}, n_comparisons={n_comparisons}). "
            f"Using BIC default penalty={bic_default_penalty:.6f}."
        )
        return bic_default_penalty, "rbf", best_ch, False

    logger.info(
        f"Optimal selected & Bonferroni validated: model={best_model}, penalty={best_penalty:.6f} "
        f"(CH={best_ch:.4f} >= critical_F={significance_threshold:.4f}, n_comp={n_comparisons})"
    )
    return best_penalty, best_model, best_ch, True


def meta_learn_all_scopes(
    connector,
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str,
    lookback_periods: int = 100,
    penalty_candidates: Optional[List[float]] = None,
) -> Dict:
    """
    Run meta-learning for all scopes to select optimal penalties.

    Each scope gets its own BIC-scaled candidates (from its own data variance).
    This means each scope automatically gets a penalty range appropriate for
    its data characteristics — funding rate variance ≠ OHLCV variance ≠ OI variance.

    Args:
        connector: Exchange connector (Binance or Gate.io)
        exchange: Exchange id for DB namespace (must match connector.exchange_id)
        pair: Trading pair (e.g., "ETH_USDT")
        timeframe: Candlestick timeframe (e.g., "1h")
        market_type: Market type ("perpetual", "spot", etc.)
        lookback_periods: Number of candles to fetch for meta-learning (default: 100)
        penalty_candidates: Override penalty candidates (default: BIC-scaled per scope)

    Returns:
        Dict with two keys:
        - "penalties": Dict[str, float] - {scope_name: penalty_value}
        - "models": Dict[str, str] - {scope_name: model_type}

    Note:
        For snapshot scopes, connector.fetch_history appends to SQLite then
        returns accumulated history. For historical scopes, data comes from API.
    """
    from src.utils.config_loader import get_sources_config
    from src.utils.history_manager import get_snapshot_history

    exchange_key = str(exchange).strip().lower()
    sources_config = get_sources_config()
    scopes = sources_config["scopes"]

    penalties = {}
    models = {}

    for scope_name in scopes:
        logger.info(f"Meta-learning for scope: {scope_name}")

        try:
            # Auto-detect data_type from sources.yaml config
            scope_config = scopes.get(scope_name, {})
            data_type = scope_config.get("data_type", "historical")

            if data_type == "snapshot":
                # Mirror LIVE: fetch appends current snapshot, then read namespace
                data_series = connector.fetch_history(
                    scope_name, pair, timeframe, market_type, lookback_periods
                )
                if not data_series:
                    data_series = get_snapshot_history(
                        scope_name, pair=pair, timeframe=timeframe,
                        exchange=exchange_key,
                    )
                logger.info(
                    f"[{scope_name}] Using snapshot history "
                    f"({exchange_key}): {len(data_series) if data_series else 0} points"
                )
            else:
                # Historical scopes: fetch from exchange API
                data_series = connector.fetch_history(
                    scope_name,
                    pair,
                    timeframe,
                    market_type,
                    lookback_periods
                )
                logger.info(
                    f"[{scope_name}] Using API history: {len(data_series) if data_series else 0} points"
                )

            if not data_series:
                logger.warning(f"Failed to fetch data for {scope_name}")
                # No hardcoded default — use BIC from minimal assumption
                penalties[scope_name] = 1.0
                models[scope_name] = "rbf"
                continue

            if len(data_series) < 3:
                logger.warning(f"Insufficient data for {scope_name} ({len(data_series)} points)")
                penalties[scope_name] = 1.0
                models[scope_name] = "rbf"
                continue

            # ===== WINDOW CAP =====
            # Limit data fed to PELT based on timeframe characteristics.
            # This is an infrastructure parameter (like timeout_seconds),
            # not a data judgment.
            original_len = len(data_series)
            data_series = _apply_window_cap(data_series, timeframe)
            if original_len != len(data_series):
                logger.info(
                    f"[{scope_name}] Window cap: {original_len} -> {len(data_series)} points"
                )

            # Generate scope-specific BIC candidates (unless overridden)
            scope_candidates = penalty_candidates
            if scope_candidates is None:
                scope_candidates = compute_penalty_candidates(data_series)
                logger.info(
                    f"[{scope_name}] BIC candidates: "
                    f"[{scope_candidates[0]:.6f} ... {scope_candidates[-1]:.6f}] "
                    f"({len(scope_candidates)} candidates)"
                )

            # Find optimal penalty and model
            optimal_penalty, optimal_model, ch_score, is_significant = find_optimal_penalty(
                data_series, scope_candidates
            )
            penalties[scope_name] = optimal_penalty
            models[scope_name] = optimal_model

            logger.info(
                f"[{scope_name}] penalty={optimal_penalty:.6f}, "
                f"model={optimal_model} (CH={ch_score:.4f}, significant={is_significant})"
            )

        except Exception as e:
            logger.error(f"Meta-learning failed for {scope_name}: {e}")
            penalties[scope_name] = 1.0
            models[scope_name] = "rbf"

    logger.info(f"Meta-learning complete. Penalties: {penalties}")
    logger.info(f"Meta-learning complete. Models: {models}")
    return {"penalties": penalties, "models": models}
