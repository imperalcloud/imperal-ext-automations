"""Automations · Shared state & extension setup."""
from __future__ import annotations

import json
import logging
import os
import time as _time

import httpx

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension, ActionResult

log = logging.getLogger("automations")


# ─── Config ───────────────────────────────────────────────────────────── #

AUTH_GW           = os.getenv("IMPERAL_GATEWAY_URL", "http://104.224.88.155:8085")
AUTH_SERVICE_TOKEN = os.getenv("AUTH_SERVICE_TOKEN", "")
REDIS_URL         = os.getenv("REDIS_URL", "")


# ─── HTTP ─────────────────────────────────────────────────────────────── #

_http = None


def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            base_url=AUTH_GW,
            headers={"X-Service-Token": AUTH_SERVICE_TOKEN, "Content-Type": "application/json"},
            timeout=15.0,
        )
    return _http


# ─── Helpers ──────────────────────────────────────────────────────────── #

def _user_id(ctx) -> str:
    return ctx.user.imperal_id if hasattr(ctx, "user") and ctx.user else ""


def _tenant_id(ctx) -> str:
    if hasattr(ctx, "user") and ctx.user and hasattr(ctx.user, "tenant_id"):
        return ctx.user.tenant_id
    return "default"


# ─── Event Catalog ────────────────────────────────────────────────────── #

_event_catalog_cache: list[dict] | None = None
_event_catalog_ts: float = 0


async def _load_event_catalog() -> list[dict]:
    global _event_catalog_cache, _event_catalog_ts
    now = _time.time()
    if _event_catalog_cache and (now - _event_catalog_ts) < 300:
        return _event_catalog_cache
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        raw = await r.get("imperal:automation:event_catalog")
        await r.aclose()
        if raw:
            _event_catalog_cache = json.loads(raw)
            _event_catalog_ts = now
            return _event_catalog_cache
    except Exception:
        pass
    return _event_catalog_cache or []


def _get_valid_event_types(catalog: list[dict]) -> set[str]:
    return {e["event_type"] for e in catalog if e.get("event_type")}


def _format_catalog_for_prompt(catalog: list[dict]) -> str:
    by_app: dict[str, list] = {}
    for e in catalog:
        by_app.setdefault(e.get("app_id", "unknown"), []).append(e)
    lines = []
    for app, evts in sorted(by_app.items()):
        for ev in evts:
            desc = ev.get("description", "")[:100]
            lines.append(f"- {ev['event_type']} — {desc}" if desc else f"- {ev['event_type']}")
    return "\n".join(lines)


# ─── System Prompt ────────────────────────────────────────────────────── #

from pathlib import Path as _Path
SYSTEM_PROMPT = (_Path(__file__).parent / "system_prompt.txt").read_text()


# ─── Extension ────────────────────────────────────────────────────────── #

ext = Extension(
    "automations",
    version="1.3.0",
    capabilities=[
        # Rule CRUD (list/create/pause/resume/delete/details)
        "automations:read", "automations:write", "automations:delete",
        # Automation runtime subscribes to platform events
        "events:subscribe",
        # Rule state + event catalog persistence
        "store:read", "store:write",
        "config:read", "config:write",
        # LLM-driven rule creation from natural language
        "ai:complete",
        # Namespace umbrella for tool_automations_chat orchestration (E8)
        "automations:*",
    ],
    display_name='AI Cloud Agents',
    description=(
        'Event-driven automation rules — subscribe to platform events (email arrivals, schedule ticks, custom signals) and run multi-step actions across other extensions when conditions match.'
    ),
    icon="icon.svg",
    actions_explicit=True,
)

chat = ChatExtension(
    ext=ext,
    tool_name="tool_automations_chat",
    description=(
        "AI Cloud Agents manager — create, list, pause, resume, delete automation rules. "
        "Automations trigger on events (email received, note created, schedule) and execute actions."
    ),
    system_prompt=SYSTEM_PROMPT,
)

_original_build = chat._build_system_prompt


def _patched_build_system_prompt(ctx):
    base = _original_build(ctx)
    catalog = _event_catalog_cache or []
    if not catalog:
        return base
    return base + "\n\nAVAILABLE TRIGGER EVENTS (use EXACT event_type):\n" + _format_catalog_for_prompt(catalog)


chat._build_system_prompt = _patched_build_system_prompt


# ─── Health Check ─────────────────────────────────────────────────────── #

@ext.health_check
async def health(ctx) -> dict:
    catalog = await _load_event_catalog()
    return {"status": "ok", "version": ext.version, "event_catalog_size": len(catalog)}


# ─── Lifecycle Hooks ──────────────────────────────────────────────────── #

@ext.on_install
async def on_install(ctx):
    log.info(f"automations installed for user {ctx.user.imperal_id if ctx and hasattr(ctx, 'user') and ctx.user else 'system'}")


# ─── Event Handlers ───────────────────────────────────────────────────── #

@ext.on_event("automation.triggered")
async def on_automation_triggered(ctx, event):
    log.info(f"Automation event: rule {event.get('data', {}).get('rule_id', '?')}")
