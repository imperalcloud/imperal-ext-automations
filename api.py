"""Automations · Auth Gateway client.

Federal-clean wrapper around ``ctx.http`` for ``/v1/automations/internal/*``
endpoints. The X-Service-Token is resolved from environment (kernel sets
it on worker boot); ctx.http handles transport, retry, timeout, and the
audit chokepoint hooks per the federal contract.

The platform event catalog is published by the kernel into Redis under
``imperal:automation:event_catalog``; we read it through ``ctx.cache``
(``EventCatalog`` Pydantic model) so consumers get typed access with
TTL-managed refresh.
"""
from __future__ import annotations

import json
import logging
import os

from constants import (
    HTTP_TIMEOUT_SECONDS,
    CATALOG_CACHE_KEY,
    CATALOG_CACHE_TTL_SECONDS,
)
from models import EventCatalog, CatalogEntry

log = logging.getLogger("automations")

AUTH_GW            = os.getenv("IMPERAL_GATEWAY_URL", "http://104.224.88.155:8085")
AUTH_SERVICE_TOKEN = os.getenv("AUTH_SERVICE_TOKEN", "")
REDIS_URL          = os.getenv("REDIS_URL", "")


def _service_headers() -> dict[str, str]:
    return {
        "X-Service-Token": AUTH_SERVICE_TOKEN,
        "Content-Type":    "application/json",
    }


def _url(path: str) -> str:
    return f"{AUTH_GW}{path}"


# ─── Rule CRUD via Auth GW ────────────────────────────────────────────── #

async def list_active_rules(ctx, *, tenant_id: str) -> list[dict]:
    """Return active rule dicts for a tenant. Empty list on failure."""
    resp = await ctx.http.get(
        _url("/v1/automations/internal/active"),
        params={"tenant_id": tenant_id},
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        log.warning("list_active_rules: HTTP %s", resp.status_code)
        return []
    return resp.body if isinstance(resp.body, list) else []


async def create_rule(ctx, *, body: dict) -> dict | None:
    """Create a rule. Returns the new rule dict, or None on failure."""
    resp = await ctx.http.post(
        _url("/v1/automations/internal/create"),
        json=body,
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code in (200, 201):
        return resp.body if isinstance(resp.body, dict) else None
    log.warning("create_rule: HTTP %s body=%s", resp.status_code, resp.body)
    return None


async def patch_rule(ctx, rule_id: int, patch: dict) -> bool:
    """Patch a rule. Returns True iff HTTP 200."""
    resp = await ctx.http.patch(
        _url(f"/v1/automations/internal/{rule_id}"),
        json=patch,
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    return resp.status_code == 200


async def delete_rule(ctx, rule_id: int) -> bool:
    """Delete a rule. Returns True iff HTTP 200/204."""
    resp = await ctx.http.delete(
        _url(f"/v1/automations/internal/{rule_id}"),
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    return resp.status_code in (200, 204)


# ─── Event catalog (Redis-published by platform) ──────────────────────── #

async def _fetch_event_catalog_raw() -> list[dict]:
    """Read the kernel-published catalog directly from Redis.

    The catalog is a small JSON list; failure to read is non-fatal —
    callers receive an empty list and behave gracefully.
    """
    if not REDIS_URL:
        return []
    try:
        import redis.asyncio as aioredis  # local import — keeps cold path light
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            raw = await r.get("imperal:automation:event_catalog")
        finally:
            await r.aclose()
    except Exception as exc:
        log.warning("event catalog fetch failed: %s", exc, exc_info=True)
        return []

    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("event catalog JSON decode failed: %s", exc)
        return []


async def load_event_catalog_cached(ctx) -> EventCatalog:
    """Typed catalog read through ctx.cache (TTL-managed)."""
    async def _fetch() -> EventCatalog:
        raw = await _fetch_event_catalog_raw()
        return EventCatalog(entries=[
            CatalogEntry(
                event_type=e.get("event_type", ""),
                description=e.get("description", ""),
                app_id=e.get("app_id", ""),
            )
            for e in raw
            if e.get("event_type")
        ])

    return await ctx.cache.get_or_fetch(
        key=CATALOG_CACHE_KEY,
        model=EventCatalog,
        fetcher=_fetch,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )
