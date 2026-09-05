import sys
from datetime import datetime, timezone

# Color is auto-detected from whether stdout is a real terminal — piping output to a
# file or another process (e.g. `python script.py > log.txt`) gets plain text, since
# raw escape codes in a log file are noise, not signal.
_COLOR = sys.stdout.isatty()

RESET = "\033[0m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""
CYAN = "\033[36m" if _COLOR else ""
LIGHT_GREEN = "\033[92m" if _COLOR else ""  # everything but the header/route line
RED = "\033[31m" if _COLOR else ""  # failures only, so they still stand out


def _can_encode(text: str) -> bool:
    try:
        text.encode(sys.stdout.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


# Some Windows setups leave stdout on a legacy codepage (cp1252) that can't encode
# these at all — print() would crash outright, not just look wrong. Verified: this
# exact crash reproduced with UnicodeEncodeError on a plain `print("↓")` under cp1252.
# Falling back to plain ASCII here means the output looks less nice on those setups,
# but never crashes regardless of what the destination terminal/encoding turns out to be.
_UNICODE_OK = _can_encode("→✓✗├└─│·")

ARROW_RIGHT = "→" if _UNICODE_OK else "->"
CHECK = "✓" if _UNICODE_OK else "OK"
CROSS = "✗" if _UNICODE_OK else "x"
TREE_MID = "├─" if _UNICODE_OK else "|-"
TREE_END = "└─" if _UNICODE_OK else "'-"
PIPE = "│" if _UNICODE_OK else "|"
DOT = "·" if _UNICODE_OK else "-"


def colored(text: str, *codes: str) -> str:
    if not codes or not _COLOR:
        return text
    return "".join(codes) + text + RESET


def fmt_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def fmt_dt_str(dt: str) -> str:
    """'2026-09-04T14:05:00Z' -> '2026-09-04 14:05:00' — reformats a row's own ISO
    datetime string for display. No parse/reformat round trip through datetime: the
    string is already well-formed (every provider's format() guarantees ISO 8601 UTC),
    this is purely a punctuation swap for readability. Live-mode fetches show full
    time-of-day (unlike backtest's date-only fmt_date) since a `length`-based window
    is often well under a day — collapsing to just the date would make every row look
    like it landed on the same instant."""
    return dt.replace("T", " ").rstrip("Z")


def fmt_time_only(dt: str) -> str:
    """'2026-09-04T14:24:00Z' -> '14:24:00' — just the time-of-day, for a live watch
    session's per-update line. The date is dropped deliberately there (unlike
    fmt_dt_str): a running watch session prints one line per bar close for as long as
    it's alive, all in quick succession — the date practically never changes within
    that log, so repeating it on every line would be pure noise."""
    return dt.split("T", 1)[1].rstrip("Z") if "T" in dt else dt
