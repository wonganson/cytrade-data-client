import time

import pytest

from cytrade_client.client import _parse_time


def test_parse_time_passes_through_int_unchanged():
    assert _parse_time(1672531200000, param_name="start_time") == 1672531200000


def test_parse_time_passes_through_none_unchanged():
    assert _parse_time(None, param_name="start_time") is None


@pytest.mark.parametrize("text", ["now", "NOW", "Now", "  now  "])
def test_parse_time_now_is_case_insensitive_and_trims_whitespace(text):
    before = int(time.time() * 1000)
    result = _parse_time(text, param_name="end_time")
    after = int(time.time() * 1000)
    assert before <= result <= after


def test_parse_time_date_only_string_means_utc_midnight():
    # 2023-01-01T00:00:00 UTC is a fixed, well-known epoch value.
    assert _parse_time("2023-01-01", param_name="start_time") == 1672531200000


def test_parse_time_date_and_time_with_space_separator():
    assert _parse_time("2023-01-01 12:00:00", param_name="start_time") == 1672531200000 + 12 * 3600 * 1000


def test_parse_time_iso_8601_with_t_separator():
    assert _parse_time("2023-01-01T12:00:00", param_name="start_time") == 1672531200000 + 12 * 3600 * 1000


def test_parse_time_is_utc_regardless_of_local_timezone(monkeypatch):
    # Simulate a non-UTC local timezone and confirm the parsed value doesn't shift —
    # this is the whole point: a caller's machine timezone must never change which
    # real moment a date string refers to.
    monkeypatch.setenv("TZ", "America/New_York")
    try:
        time.tzset()
    except AttributeError:
        pytest.skip("time.tzset() unavailable on this platform (Windows)")
    try:
        assert _parse_time("2023-01-01", param_name="start_time") == 1672531200000
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_parse_time_rejects_unrecognized_string_format():
    with pytest.raises(ValueError):
        _parse_time("01/01/2023", param_name="start_time")


def test_parse_time_rejects_unsupported_type():
    with pytest.raises(TypeError):
        _parse_time(1234.5, param_name="start_time")
