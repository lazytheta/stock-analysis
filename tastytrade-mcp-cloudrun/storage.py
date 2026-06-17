"""Supabase REST wrapper for MCP user token storage.

Table: mcp_user_tokens
  user_id          UUID primary key
  tt_refresh_token TEXT    — TastyTrade refresh token (long-lived)
  tt_account_id    TEXT    — selected TastyTrade account (nullable until picker)
  created_at       TIMESTAMPTZ
  updated_at       TIMESTAMPTZ

Service-role key bypasses RLS; only this server has it (via env).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_TIMEOUT = 10.0


class StorageError(Exception):
    pass


def _base_url() -> str:
    v = os.environ.get("SUPABASE_URL", "")
    if not v:
        raise StorageError("SUPABASE_URL env var not set")
    return v.rstrip("/")


def _service_key() -> str:
    v = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not v:
        raise StorageError("SUPABASE_SERVICE_ROLE_KEY env var not set")
    return v


def _headers() -> dict[str, str]:
    key = _service_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def create_user_with_tt_token(tt_refresh_token: str) -> str:
    """Insert a new user row, return its generated UUID."""
    url = f"{_base_url()}/rest/v1/mcp_user_tokens"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
        resp = await http.post(
            url,
            json={"tt_refresh_token": tt_refresh_token},
            headers={**_headers(), "Prefer": "return=representation"},
        )
    if resp.status_code >= 400:
        raise StorageError(f"create_user failed: {resp.status_code} {resp.text[:300]}")
    rows = resp.json()
    if not rows:
        raise StorageError("create_user returned no rows")
    return rows[0]["user_id"]


async def get_user(user_id: str) -> dict | None:
    url = f"{_base_url()}/rest/v1/mcp_user_tokens"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
        resp = await http.get(
            url,
            params={"user_id": f"eq.{user_id}", "select": "*"},
            headers=_headers(),
        )
    if resp.status_code >= 400:
        raise StorageError(f"get_user failed: {resp.status_code} {resp.text[:300]}")
    rows = resp.json()
    return rows[0] if rows else None


async def update_user(user_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    url = f"{_base_url()}/rest/v1/mcp_user_tokens"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http:
        resp = await http.patch(
            url,
            params={"user_id": f"eq.{user_id}"},
            json=fields,
            headers=_headers(),
        )
    if resp.status_code >= 400:
        raise StorageError(f"update_user failed: {resp.status_code} {resp.text[:300]}")


async def set_account_id(user_id: str, account_id: str) -> None:
    await update_user(user_id, tt_account_id=account_id)


async def set_tt_refresh_token(user_id: str, refresh_token: str) -> None:
    await update_user(user_id, tt_refresh_token=refresh_token)
