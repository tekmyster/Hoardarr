from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from hoardarr.api.client import client_identity
from hoardarr.api.rate_limit import AttemptLimiter, RateLimitExceeded


def test_attempt_limiter_is_bounded_and_does_not_allocate_on_check() -> None:
    limiter = AttemptLimiter(attempts=2, maximum_keys=3)
    for index in range(100):
        limiter.check(f"unused-{index}")
    assert limiter.tracked_keys == 0

    for index in range(10):
        limiter.record_failure(f"failed-{index}")
    assert limiter.tracked_keys == 3
    limiter.record_failure("failed-9")
    with pytest.raises(RateLimitExceeded):
        limiter.check("failed-9")


def test_attempt_reservations_can_be_refunded_without_erasing_failures() -> None:
    limiter = AttemptLimiter(attempts=3)
    limiter.record_failure("client")
    limiter.consume("client")
    limiter.refund("client")
    limiter.consume("client")
    limiter.consume("client")
    with pytest.raises(RateLimitExceeded):
        limiter.consume("client")


def test_client_identity_uses_rightmost_non_proxy_address() -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(trusted_proxy_addresses=("127.0.0.1", "::1"))
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.99, 192.0.2.20, 127.0.0.1")],
            "client": ("127.0.0.1", 12345),
            "app": app,
        }
    )
    assert client_identity(request) == "192.0.2.20"
