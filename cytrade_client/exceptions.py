from typing import Optional


class CytradeAPIError(Exception):
    """
    Raised when the gateway rejects or fails a request. `status_code` mirrors the HTTP
    status. `max_allowed_ms`/`max_allowed_rows` are set only when the gateway rejected
    the request for exceeding its per-call size limit — that limit is computed per
    provider/interval on the gateway, not a fixed number, so read it from here rather
    than assuming one.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        max_allowed_ms: Optional[int] = None,
        max_allowed_rows: Optional[int] = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.max_allowed_ms = max_allowed_ms
        self.max_allowed_rows = max_allowed_rows
        super().__init__(f"[{status_code}] {detail}")


class QuotaExceededError(CytradeAPIError):
    """The API key's monthly call limit has been reached (HTTP 429)."""
