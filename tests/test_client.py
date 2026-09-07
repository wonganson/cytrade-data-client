import pytest

from cytrade_client.client import DataFetcher, _parse_route
from cytrade_client.exceptions import CytradeAPIError, QuotaExceededError


class FakeResponse:
    """Stand-in for requests.Response — _raise_for_status only touches these three."""

    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


@pytest.fixture
def fetcher():
    return DataFetcher(api_key="test-key", base_url="http://localhost:8420", verbose=False)


def test_parse_route_splits_provider_path_and_params():
    provider, path, params = _parse_route(
        "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"
    )
    assert provider == "bybit-direct"
    assert path == "/v5/market/kline"
    assert params == {"category": "linear", "symbol": "BTCUSDT", "interval": "60"}


def test_parse_route_with_no_query_string():
    provider, path, params = _parse_route("ccxt|bybit/markets")
    assert provider == "ccxt"
    assert path == "bybit/markets"
    assert params == {}


def test_parse_route_without_pipe_raises():
    with pytest.raises(ValueError):
        _parse_route("bybit-direct/v5/market/kline")


def test_raise_for_status_ok_is_a_noop(fetcher):
    fetcher._raise_for_status(FakeResponse(200))  # must not raise


def test_raise_for_status_429_raises_quota_exceeded(fetcher):
    resp = FakeResponse(429, json_body={"detail": {"message": "monthly limit reached"}})
    with pytest.raises(QuotaExceededError) as exc_info:
        fetcher._raise_for_status(resp)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "monthly limit reached"


def test_raise_for_status_400_raises_cytrade_api_error_with_limits(fetcher):
    resp = FakeResponse(
        400,
        json_body={
            "detail": {
                "message": "range too large",
                "max_allowed_ms": 3600_000,
                "max_allowed_rows": 1000,
            }
        },
    )
    with pytest.raises(CytradeAPIError) as exc_info:
        fetcher._raise_for_status(resp)
    err = exc_info.value
    assert not isinstance(err, QuotaExceededError)
    assert err.status_code == 400
    assert err.max_allowed_ms == 3600_000
    assert err.max_allowed_rows == 1000


def test_raise_for_status_plain_text_detail(fetcher):
    resp = FakeResponse(500, json_body={"detail": "internal error"})
    with pytest.raises(CytradeAPIError) as exc_info:
        fetcher._raise_for_status(resp)
    assert exc_info.value.detail == "internal error"
    assert exc_info.value.max_allowed_ms is None


def test_raise_for_status_non_json_body_falls_back_to_text(fetcher):
    resp = FakeResponse(502, json_body=None, text="Bad Gateway")
    with pytest.raises(CytradeAPIError) as exc_info:
        fetcher._raise_for_status(resp)
    assert exc_info.value.detail == "Bad Gateway"


def test_get_backtest_rejects_length(fetcher):
    with pytest.raises(ValueError):
        fetcher.get("bybit-direct|/v5/market/kline", mode="backtest", length=10)


def test_get_live_requires_length(fetcher):
    with pytest.raises(ValueError):
        fetcher.get("bybit-direct|/v5/market/kline", mode="live")


def test_get_live_rejects_start_end_time(fetcher):
    with pytest.raises(ValueError):
        fetcher.get("bybit-direct|/v5/market/kline", mode="live", length=10, start_time=1)


def test_get_backtest_accepts_human_readable_date_strings(fetcher):
    from unittest.mock import Mock

    fetcher._get_backtest = Mock(return_value="sentinel")
    route = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

    fetcher.get(route, mode="backtest", start_time="2023-01-01", end_time="2023-01-02")

    fetcher._get_backtest.assert_called_once_with(
        route, "bybit-direct", "/v5/market/kline", {"category": "linear", "symbol": "BTCUSDT", "interval": "60"},
        1672531200000, 1672617600000,
    )


def test_get_unknown_mode_raises(fetcher):
    with pytest.raises(ValueError):
        fetcher.get("bybit-direct|/v5/market/kline", mode="bogus")
