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
    CAPABILITY_CACHE_KEY,
    USER_ROLE_CACHE_KEY,
    USER_ROLE_CACHE_TTL_SECONDS,
)
from models import (
    EventCatalog, CatalogEntry, UserRoleSnapshot,
    CapabilityCatalog, CapabilityEntry,
)

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
    """Return ALL rule dicts for a tenant (active + paused + error).

    Despite the legacy name, this hits ``/v1/automations/internal/all``
    so panels and admin-view see every rule status — the older
    ``/active`` endpoint silently filtered out paused ones.
    """
    resp = await ctx.http.get(
        _url("/v1/automations/internal/all"),
        params={"tenant_id": tenant_id},
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        log.warning("list_active_rules: HTTP %s", resp.status_code)
        return []
    return resp.body if isinstance(resp.body, list) else []


async def create_rule(ctx, *, body: dict) -> dict | None:
    """Create a rule. Returns the new rule dict, or None on failure.

    On quota breach (HTTP 429) returns
    ``{'error': 'quota_exceeded', 'quota': {...}}`` so callers can surface
    the structured quota facts to the narrator without coupling to HTTP details.
    """
    resp = await ctx.http.post(
        _url("/v1/automations/internal/create"),
        json=body,
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code in (200, 201):
        return resp.body if isinstance(resp.body, dict) else None
    if resp.status_code == 429 and isinstance(resp.body, dict):
        detail = resp.body.get("detail", resp.body)
        if isinstance(detail, dict) and detail.get("error_code") == "AUTOMATION_QUOTA_EXCEEDED":
            return {"error": "quota_exceeded", "quota": detail.get("quota", {})}
    log.warning("create_rule: HTTP %s body=%s", resp.status_code, resp.body)
    return None


async def get_quota(ctx) -> dict:
    """Effective automation cap + usage for the current user (cascade-resolved on GW).

    Returns a dict with ``cap``, ``used``, ``remaining``, ``unlimited``, ``plan``.
    Returns an empty dict on failure (fail-soft — caller receives degraded skeleton).
    """
    user_id   = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")
    try:
        resp = await ctx.http.get(
            _url("/v1/automations/internal/quota"),
            params={"user_id": user_id, "tenant_id": tenant_id},
            headers=_service_headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code == 200 and isinstance(resp.body, dict):
            return resp.body
    except Exception as exc:
        log.warning("get_quota failed: %s", exc, exc_info=True)
    return {}


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


# ─── Capability catalog (Redis-published by platform) ─────────────────── #

async def _fetch_capability_catalog_raw() -> list[dict]:
    """Read the kernel-published capability catalog directly from Redis.

    Mirrors _fetch_event_catalog_raw: a small JSON list of per-app invokable
    tools + param names; failure is non-fatal (empty list -> NL fallback).
    """
    if not REDIS_URL:
        return []
    try:
        import redis.asyncio as aioredis  # local import — keeps cold path light
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            raw = await r.get("imperal:automation:capability_catalog")
        finally:
            await r.aclose()
    except Exception as exc:
        log.warning("capability catalog fetch failed: %s", exc, exc_info=True)
        return []

    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("capability catalog JSON decode failed: %s", exc)
        return []


async def load_capability_catalog_cached(ctx) -> CapabilityCatalog:
    """Typed capability catalog read through ctx.cache (TTL-managed)."""
    async def _fetch() -> CapabilityCatalog:
        raw = await _fetch_capability_catalog_raw()
        return CapabilityCatalog(entries=[
            CapabilityEntry(
                app_id=e.get("app_id", ""),
                tool=e.get("tool", ""),
                action_type=e.get("action_type", "read"),
                required_params=e.get("required_params") or [],
                optional_params=e.get("optional_params") or [],
            )
            for e in raw
            if e.get("tool")
        ])

    return await ctx.cache.get_or_fetch(
        key=CAPABILITY_CACHE_KEY,
        model=CapabilityCatalog,
        fetcher=_fetch,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


# ─── Authoritative user role (workaround for kernel ctx.user.role drift) ── #

async def fetch_user_role_cached(ctx) -> str:
    """Return the user's role from auth-gw, cached per-user via ctx.cache.

    The kernel-side context-factory pulls role from a stale-prone path
    (``imperal:user_info:<uid>`` Redis cache + fallback default), so
    extensions that need authoritative role for capability gating
    should query auth-gw directly. ``ctx.cache`` is per-(app_id, user)
    by design so the entry is correctly user-scoped.
    """
    async def _fetch() -> UserRoleSnapshot:
        resp = await ctx.http.get(
            _url(f"/v1/users/{ctx.user.imperal_id}"),
            headers=_service_headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code == 200 and isinstance(resp.body, dict):
            return UserRoleSnapshot(role=resp.body.get("role", ""))
        return UserRoleSnapshot(role="")

    snap = await ctx.cache.get_or_fetch(
        key=USER_ROLE_CACHE_KEY,
        model=UserRoleSnapshot,
        fetcher=_fetch,
        ttl_seconds=USER_ROLE_CACHE_TTL_SECONDS,
    )
    return snap.role
