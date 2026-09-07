from unittest.mock import Mock

import pandas as pd
import pytest

from cytrade_client.client import DataFetcher
from cytrade_client.exceptions import CytradeAPIError, QuotaExceededError


def _bars(start: str, count: int, interval_minutes: int = 1) -> pd.DataFrame:
    """Ascending-by-datetime bars, matching what a real gateway response looks like —
    oldest first, newest last (see Watcher._compute_delay / _footer_live, both of
    which assume this ordering)."""
    idx = pd.date_range(start, periods=count, freq=f"{interval_minutes}min", tz="UTC")
    return pd.DataFrame({"datetime": idx.strftime("%Y-%m-%dT%H:%M:%SZ"), "close": range(count)})


@pytest.fixture
def fetcher():
    return DataFetcher(api_key="test-key", base_url="http://localhost:8420", verbose=False)


def test_get_live_no_chunking_needed_when_length_fits(fetcher):
    fetcher._request = Mock(return_value=_bars("2024-01-01", 700))
    df = fetcher._get_live("bybit-direct", "/v5/market/kline", {}, length=700, verbose_note=False)
    assert len(df) == 700
    fetcher._request.assert_called_once_with("bybit-direct", "/v5/market/kline", {}, length=700)


def test_get_live_stitches_chunks_to_reach_requested_length(fetcher):
    too_big = CytradeAPIError(400, "length (2000) exceeds the 1000-row max per call for this endpoint.", max_allowed_rows=1000)
    probe = _bars("2024-01-02", 1000)  # newest 1000 bars
    older_chunk = _bars("2024-01-01", 1000)  # the 1000 bars right before those

    def fake_request(provider, path, params, start_time=None, end_time=None, length=None):
        if length == 2000:
            raise too_big
        if length == 1000:
            return probe
        assert start_time is not None and end_time is not None
        return older_chunk

    fetcher._request = Mock(side_effect=fake_request)
    df = fetcher._get_live("ccxt", "bybit/candle_history", {"symbol": "BTC/USDT:USDT"}, length=2000, verbose_note=False)

    assert len(df) == 2000
    assert df["datetime"].is_monotonic_increasing
    assert df["datetime"].iloc[-1] == probe["datetime"].iloc[-1]  # newest bar preserved
    assert df["datetime"].iloc[0] == older_chunk["datetime"].iloc[0]  # walked back far enough


def test_get_live_stops_at_start_of_history_and_returns_what_exists(fetcher):
    too_big = CytradeAPIError(400, "length (2000) exceeds the 1000-row max per call for this endpoint.", max_allowed_rows=1000)
    probe = _bars("2024-01-01", 500)  # only 500 bars exist at all

    def fake_request(provider, path, params, start_time=None, end_time=None, length=None):
        if length == 2000:
            raise too_big
        if length == 1000:
            return probe
        return pd.DataFrame()  # reached start of history

    fetcher._request = Mock(side_effect=fake_request)
    df = fetcher._get_live("bybit-direct", "/v5/market/kline", {}, length=2000, verbose_note=False)

    assert len(df) == 500  # fewer than requested, no padding, no error


def test_get_live_falls_back_when_probe_has_fewer_than_two_rows(fetcher):
    too_big = CytradeAPIError(400, "too big", max_allowed_rows=1000)

    def fake_request(provider, path, params, start_time=None, end_time=None, length=None):
        if length == 2000:
            raise too_big
        return _bars("2024-01-01", 1)

    fetcher._request = Mock(side_effect=fake_request)
    df = fetcher._get_live("bybit-direct", "/v5/market/kline", {}, length=2000, verbose_note=False)

    assert len(df) == 1


def test_get_live_reraises_errors_that_are_not_a_row_cap_rejection(fetcher):
    fetcher._request = Mock(side_effect=QuotaExceededError(429, "monthly limit reached"))
    with pytest.raises(QuotaExceededError):
        fetcher._get_live("bybit-direct", "/v5/market/kline", {}, length=2000, verbose_note=False)


def test_get_live_reraises_when_no_max_allowed_rows_to_chunk_by(fetcher):
    fetcher._request = Mock(side_effect=CytradeAPIError(400, "length isn't supported for this endpoint"))
    with pytest.raises(CytradeAPIError):
        fetcher._get_live("ccxt", "bybit/markets", {}, length=2000, verbose_note=False)
