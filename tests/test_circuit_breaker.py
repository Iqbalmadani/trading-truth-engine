"""
Unit Tests for Layer 4 Circuit Breaker & ESS Calculation
"""

import pytest
import numpy as np
from src.layer4.circuit_breaker import (
    compute_effective_sample_size,
    evaluate_performance,
)


def test_ess_bounds_and_basic():
    # Short series
    assert compute_effective_sample_size([1, 0, 1]) == 3.0
    assert compute_effective_sample_size([]) == 1.0

    # Constant series (all wins)
    all_wins = [1] * 50
    ess_wins = compute_effective_sample_size(all_wins)
    assert ess_wins == 50.0


def test_ess_autocorrelated_series():
    # Highly correlated blocks of wins and losses
    correlated = [1] * 10 + [0] * 10 + [1] * 10 + [0] * 10
    ess = compute_effective_sample_size(correlated)
    assert 1.0 <= ess <= len(correlated)
    # Autocorrelation should reduce effective sample size
    assert ess < len(correlated)


def test_ess_independent_series():
    # Independent Bernoulli sequence
    np.random.seed(42)
    indep = list(np.random.binomial(1, 0.5, 100))
    ess = compute_effective_sample_size(indep)
    # ESS should be close to N (within reasonable sampling variation)
    assert 0.7 * len(indep) <= ess <= len(indep)


def test_circuit_breaker_pass_through_on_empty():
    perf = evaluate_performance("test_exchange", "TEST_USDT", "1h")
    assert perf["should_trade"] is True
