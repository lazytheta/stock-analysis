"""JWT signing + verification helpers. OAuth bridge functions added in Task 3."""

from __future__ import annotations

import os
import time

import jwt


def _signing_key() -> str:
    key = os.environ.get("JWT_SIGNING_KEY", "")
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY env var not configured")
    return key


def sign_jwt(claims: dict, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def verify_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except Exception:
        return None
