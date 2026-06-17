"""TastyTrade REST API client (multi-user).

Each call takes user_id; refresh and account-id come from Supabase.
TT access tokens are cached per-user in-memory per worker (~14min TTL).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from storage import get_user, set_tt_refresh_token

API_BASE = os.environ.get("TASTYTRADE_API_BASE", "https://api.tastyworks.com")
TOKEN_URL = "https://api.tastytrade.com/oauth/token"
DEFAULT_TIMEOUT = 12.0


class TastyTradeError(Exception):
    pass


def _client_id() -> str:
    v = os.environ.get("TASTYTRADE_CLIENT_ID", "")
    if not v:
        raise TastyTradeError("TASTYTRADE_CLIENT_ID env var not set")
    return v


def _client_secret() -> str:
    v = os.environ.get("TASTYTRADE_CLIENT_SECRET", "")
    if not v:
        raise TastyTradeError("TASTYTRADE_CLIENT_SECRET env var not set")
    return v


class _TokenCache:
    """Per-user access-token cache with expiry."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_or_refresh(self, user_id: str) -> tuple[str, str]:
        """Return (access_token, account_id)."""
        async with self._lock:
            row = await get_user(user_id)
            if not row:
                raise TastyTradeError(
                    "Geen TastyTrade-credentials gevonden. Re-authoriseer via de connector."
                )
            account_id = row.get("tt_account_id")
            if not account_id:
                raise TastyTradeError(
                    "Geen TastyTrade-account geselecteerd. Re-authoriseer of kies een account."
                )

            cached = self._cache.get(user_id)
            if cached and time.time() < cached[1] - 30:
                return cached[0], account_id

            refresh_token = row["tt_refresh_token"]
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
                resp = await http.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": _client_id(),
                        "client_secret": _client_secret(),
                    },
                )
            if resp.status_code >= 400:
                raise TastyTradeError(
                    f"OAuth refresh failed: {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json()
            access_token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 900))
            self._cache[user_id] = (access_token, time.time() + expires_in)

            # If TT rotated the refresh token, persist the new one.
            new_refresh = payload.get("refresh_token")
            if new_refresh and new_refresh != refresh_token:
                try:
                    await set_tt_refresh_token(user_id, new_refresh)
                except Exception:
                    pass

            return access_token, account_id


_tokens = _TokenCache()


async def _request(
    user_id: str, method: str, path: str, params: dict | None = None
) -> dict[str, Any]:
    access_token, _ = await _tokens.get_or_refresh(user_id)
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
        resp = await http.request(
            method,
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    if resp.status_code == 401:
        # access token may be stale; force refresh next call
        _tokens._cache.pop(user_id, None)
        raise TastyTradeError("TastyTrade authenticatie afgewezen — opnieuw inloggen via de connector.")
    if resp.status_code >= 400:
        raise TastyTradeError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def _account_id(user_id: str) -> str:
    _, acc = await _tokens.get_or_refresh(user_id)
    return acc


# ---- Account endpoints ----

async def get_balances(user_id: str) -> dict:
    acc = await _account_id(user_id)
    body = await _request(user_id, "GET", f"/accounts/{acc}/balances")
    return body.get("data", {})


async def get_positions(user_id: str) -> list[dict]:
    acc = await _account_id(user_id)
    body = await _request(user_id, "GET", f"/accounts/{acc}/positions")
    return body.get("data", {}).get("items", [])


async def get_transactions(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    symbol: str | None = None,
    types: list[str] | None = None,
    per_page: int = 250,
    page_offset: int = 0,
) -> dict:
    acc = await _account_id(user_id)
    params: dict[str, Any] = {"per-page": per_page, "page-offset": page_offset}
    if start_date:
        params["start-date"] = start_date
    if end_date:
        params["end-date"] = end_date
    if symbol:
        params["symbol"] = symbol
    if types:
        params["types[]"] = types
    body = await _request(user_id, "GET", f"/accounts/{acc}/transactions", params=params)
    return {
        "items": body.get("data", {}).get("items", []),
        "pagination": body.get("pagination", {}),
    }


async def get_orders(user_id: str, status: str | None = None) -> list[dict]:
    acc = await _account_id(user_id)
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    body = await _request(user_id, "GET", f"/accounts/{acc}/orders", params=params or None)
    return body.get("data", {}).get("items", [])


# ---- Market data ----

# TT's /market-data/by-type expects singular param names (equity, equity-option,
# index, future, future-option, cryptocurrency). Plurals return empty results
# silently — that bit us until 2026-04-30.
_INSTRUMENT_TYPE_TO_PARAM = {
    "equities": "equity",
    "equity-options": "equity-option",
    "indices": "index",
    "futures": "future",
    "future-options": "future-option",
    "cryptocurrencies": "cryptocurrency",
    # Allow callers who already pass the singular form
    "equity": "equity",
    "equity-option": "equity-option",
    "index": "index",
    "future": "future",
    "future-option": "future-option",
    "cryptocurrency": "cryptocurrency",
}


async def get_quotes(
    user_id: str, symbols: list[str], instrument_type: str = "equities"
) -> list[dict]:
    param_name = _INSTRUMENT_TYPE_TO_PARAM.get(instrument_type, instrument_type)
    # httpx serializes lists into repeated query params (?equity=AAPL&equity=MSFT)
    # which is what TT expects.
    params = {param_name: list(symbols)}
    body = await _request(user_id, "GET", "/market-data/by-type", params=params)
    return body.get("data", {}).get("items", [])


async def get_market_metrics(user_id: str, symbols: list[str]) -> list[dict]:
    params = {"symbols": ",".join(symbols)}
    body = await _request(user_id, "GET", "/market-metrics", params=params)
    return body.get("data", {}).get("items", [])


async def get_option_chain_nested(user_id: str, symbol: str) -> dict:
    body = await _request(user_id, "GET", f"/option-chains/{symbol}/nested")
    return body.get("data", {})


# ---- Watchlists ----

async def get_watchlists(user_id: str) -> list[dict]:
    body = await _request(user_id, "GET", "/watchlists")
    return body.get("data", {}).get("items", [])


async def get_watchlist(user_id: str, name: str) -> dict:
    body = await _request(user_id, "GET", f"/watchlists/{name}")
    return body.get("data", {})
