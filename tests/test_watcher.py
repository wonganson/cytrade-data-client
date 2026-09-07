import time

import pandas as pd
import pytest

from cytrade_client.client import DataFetcher
from cytrade_client.watcher import Watcher


@pytest.fixture
def watcher():
    fetcher = DataFetcher(api_key="test-key", base_url="http://localhost:8420", verbose=False)
    return Watcher(fetcher, route="bybit-direct|/v5/market/kline", length=700, poll_interval=30, on_update=None)


def _df_with_bars(*timestamps):
    return pd.DataFrame({"datetime": pd.to_datetime(list(timestamps))})


def test_compute_delay_falls_back_when_fewer_than_two_rows(watcher):
    assert watcher._compute_delay(_df_with_bars("2024-01-01 00:00:00")) == 30.0
    assert watcher._compute_delay(pd.DataFrame()) == 30.0


def test_compute_delay_falls_back_without_datetime_column(watcher):
    assert watcher._compute_delay(pd.DataFrame({"close": [1, 2]})) == 30.0


def test_compute_delay_schedules_after_next_bar_close(watcher):
    now = pd.Timestamp.now(tz="UTC")
    last_bar = now - pd.Timedelta(seconds=10)
    prev_bar = last_bar - pd.Timedelta(hours=1)  # 1h bar spacing
    df = _df_with_bars(prev_bar, last_bar)

    delay = watcher._compute_delay(df)

    # next bar closes at last_bar + 2*interval; delay is time until then + buffer
    expected_next_close = last_bar.timestamp() + 2 * 3600
    expected_delay = expected_next_close - time.time() + watcher._poll_interval * 0 + 3.0
    assert delay == pytest.approx(expected_delay, abs=2)
    assert delay >= watcher._poll_interval  # far in the future, not the 30s fallback


def test_compute_delay_never_goes_below_floor(watcher):
    now = pd.Timestamp.now(tz="UTC")
    # bars spaced so the "next close" is already long past -> should clamp to the 1s floor
    df = _df_with_bars(now - pd.Timedelta(hours=2), now - pd.Timedelta(hours=1, minutes=59))
    delay = watcher._compute_delay(df)
    assert delay == 1.0


def test_compute_delay_falls_back_on_non_increasing_timestamps(watcher):
    now = pd.Timestamp.now(tz="UTC")
    df = _df_with_bars(now, now)  # zero spacing
    assert watcher._compute_delay(df) == 30.0
