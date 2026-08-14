"""API key authentication dependency for NebulaStream control plane.

Usage::

    from auth import require_api_key

    @app.post("/chaos/inject", dependencies=[Depends(require_api_key)])
    async def chaos_inject(...):
        ...

If ``ADMIN_API_KEY`` is not set (empty string), auth is **disabled** and all
requests are allowed through — this is intentional for local development.
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-KEY", auto_error=False)


def _get_configured_key() -> str:
    return os.environ.get("ADMIN_API_KEY", "")


async def require_api_key(
    api_key: str | None = Security(_API_KEY_HEADER),
) -> str:
    """FastAPI dependency that enforces API key auth.

    Returns the validated key on success.
    Raises HTTP 403 on invalid key.
    Bypasses check entirely if ``ADMIN_API_KEY`` env var is not set (dev mode).
    """
    configured = _get_configured_key()

    # Auth is disabled in dev mode (no key configured).
    if not configured:
        return "dev-mode-no-auth"

    if not api_key or api_key != configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-API-KEY header.",
        )
    return api_key
