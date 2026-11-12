import sys
from pathlib import Path
import time
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.verifier import Verifier


@pytest.fixture
def verifier():
    return Verifier()


def test_valid_candle_passes(verifier):
    valid_data = {
        "timestamp": int(time.time()),
        "open": 2500.0,
        "high": 2550.0,
        "low": 2480.0,
        "close": 2520.0,
        "volume": 100.5,
    }
    result = verifier._check_existence("ohlcv", valid_data, "1h")
    assert result == 1, "Valid candle data must pass existence check (1)"


def test_volume_negatif_ditolak(verifier):
    negative_vol = {
        "timestamp": int(time.time()),
        "open": 2500.0,
        "high": 2550.0,
        "low": 2480.0,
        "close": 2520.0,
        "volume": -10.0,  # Negative volume
    }
    result = verifier._check_existence("ohlcv", negative_vol, "1h")
    assert result == 0, "Negative volume must be rejected (0)"


def test_harga_nol_atau_negatif_ditolak(verifier):
    zero_price = {
        "timestamp": int(time.time()),
        "open": 0.0,  # Zero price
        "high": 2550.0,
        "low": 2480.0,
        "close": 2520.0,
        "volume": 100.0,
    }
    result = verifier._check_existence("ohlcv", zero_price, "1h")
    assert result == 0, "Zero open price must be rejected (0)"

    negative_price = {
        "timestamp": int(time.time()),
        "open": 2500.0,
        "high": 2550.0,
        "low": 2480.0,
        "close": -2520.0,  # Negative close
        "volume": 100.0,
    }
    result = verifier._check_existence("ohlcv", negative_price, "1h")
    assert result == 0, "Negative close price must be rejected (0)"


def test_gap_candle_terdeteksi_stale(verifier):
    # max_staleness is typically 2 intervals in config
    interval_seconds = 3600  # 1h
    stale_timestamp = int(time.time()) - (5 * interval_seconds)  # 5 hours old (gap/stale)

    stale_data = {
        "timestamp": stale_timestamp,
        "open": 2500.0,
        "high": 2550.0,
        "low": 2480.0,
        "close": 2520.0,
        "volume": 100.0,
    }
    result = verifier._check_existence("ohlcv", stale_data, "1h")
    assert result == 0, "Stale candle data / missing gap must fail freshness check (0)"


def test_high_less_than_low_ditolak(verifier):
    invalid_hl = {
        "timestamp": int(time.time()),
        "open": 2500.0,
        "high": 2400.0,  # High < Low
        "low": 2450.0,
        "close": 2420.0,
        "volume": 100.0,
    }
    result = verifier._check_existence("ohlcv", invalid_hl, "1h")
    assert result == 0, "High < Low must be rejected (0)"
