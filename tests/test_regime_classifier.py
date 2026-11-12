"""
Unit Tests for Layer 3 Regime Classifier

Tests multi-scale Hurst Exponent, Lo-MacKinlay Variance Ratio, and
regime classification on synthetic and edge-case series.
"""

import pytest
import numpy as np
from src.layer3.regime_classifier import (
    compute_hurst_rs,
    compute_variance_ratio,
    classify_regime,
)


def test_insufficient_data():
    # Length < 30 should return "insufficient_data"
    short_series = [100.0 + i for i in range(15)]
    assert classify_regime(short_series, min_window_length=30) == "insufficient_data"
    assert classify_regime([], min_window_length=30) == "insufficient_data"
    assert classify_regime(None, min_window_length=30) == "insufficient_data"


def test_hurst_rs_bounds():
    np.random.seed(42)
    # Random walk
    rw = np.cumprod(1.0 + np.random.normal(0, 0.01, 200))
    h = compute_hurst_rs(list(rw))
    assert 0.0 <= h <= 1.0


def test_variance_ratio_properties():
    np.random.seed(42)
    rw = np.cumprod(1.0 + np.random.normal(0, 0.01, 200))
    vr, z = compute_variance_ratio(list(rw), lag=2)
    assert isinstance(vr, float)
    assert isinstance(z, float)
    assert vr > 0.0


def test_synthetic_trending_series():
    # Strong persistent trending momentum (AR(1) positive autocorrelation)
    np.random.seed(42)
    n = 250
    returns = np.zeros(n)
    for i in range(1, n):
        returns[i] = 0.75 * returns[i-1] + np.random.normal(0, 0.01)
    
    series = 100.0 * np.exp(np.cumsum(returns))
    
    h = compute_hurst_rs(list(series))
    vr, z = compute_variance_ratio(list(series), lag=2)
    regime = classify_regime(list(series), min_window_length=30)

    assert h > 0.58
    assert vr > 1.0
    assert z > 1.96
    assert regime == "trending"


def test_synthetic_mean_reverting_series():
    # Ornstein-Uhlenbeck mean-reverting process with strong mean reversion
    np.random.seed(42)
    n = 200
    theta = 0.8  # Strong mean reversion speed
    mu = 100.0
    sigma = 1.0
    dt = 1.0
    
    x = np.zeros(n)
    x[0] = mu
    for i in range(1, n):
        dx = theta * (mu - x[i-1]) * dt + sigma * np.random.normal()
        x[i] = max(10.0, x[i-1] + dx)
        
    h = compute_hurst_rs(list(x))
    vr, z = compute_variance_ratio(list(x), lag=2)
    regime = classify_regime(list(x), min_window_length=30)

    assert h < 0.52
    assert vr < 1.0
    assert z < -1.96
    assert regime == "mean_reverting"


def test_synthetic_random_walk_ambiguous():
    # Standard random walk should fall in ambiguous/insufficient zone
    np.random.seed(99)
    rw = 100.0 * np.cumprod(1.0 + np.random.normal(0, 0.01, 100))
    regime = classify_regime(list(rw), min_window_length=30)
    # A pure random walk should not be confidently classified as trending or MR
    assert regime == "insufficient_data"
