"""
Persistent error logging to Supabase error_logs table.

Usage:
    from error_logger import log_error
    log_error("AUTH_ERROR", "Session refresh failed", page="Portfolio")
"""

import traceback
import streamlit as st


def log_error(error_type, error_message, page=None, stack_trace=None, metadata=None):
    """Log an error to the Supabase error_logs table.

    Never raises — all exceptions are silently swallowed so that error logging
    itself never breaks the app.
    """
    try:
        client = st.session_state.get("supabase_client")
        if client is None:
            return

        user = st.session_state.get("user")
        user_id = user["id"] if user and isinstance(user, dict) else None

        # Sanitize: strip tokens/passwords from error message
        sanitized_msg = _sanitize(str(error_message))

        row = {
            "error_type": str(error_type)[:50],
            "error_message": sanitized_msg[:2000],
        }
        if user_id:
            row["user_id"] = user_id
        if page:
            row["page"] = str(page)[:100]
        if stack_trace:
            row["stack_trace"] = _sanitize(str(stack_trace))[:5000]
        if metadata and isinstance(metadata, dict):
            row["metadata"] = {k: _sanitize(str(v))[:500] for k, v in metadata.items()}

        client.table("error_logs").insert(row).execute()
    except Exception:
        pass  # Never let error logging break the app


def log_error_with_trace(error_type, exc, page=None, metadata=None):
    """Convenience wrapper that extracts message and traceback from an exception."""
    log_error(
        error_type,
        str(exc),
        page=page,
        stack_trace=traceback.format_exc(),
        metadata=metadata,
    )


_SENSITIVE_PATTERNS = [
    "token", "password", "secret", "authorization", "bearer", "cookie",
    "refresh_token", "access_token", "session_token", "api_key",
]


def _sanitize(text):
    """Basic redaction of lines that look like they contain secrets."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        lower = line.lower()
        if any(pat in lower for pat in _SENSITIVE_PATTERNS):
            # Keep the key name but redact the value
            for pat in _SENSITIVE_PATTERNS:
                if pat in lower:
                    cleaned.append(f"[REDACTED — contains {pat}]")
                    break
        else:
            cleaned.append(line)
    return "\n".join(cleaned)
