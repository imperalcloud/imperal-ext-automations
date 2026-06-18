"""Automations · @ext.skeleton refresh tool (canonical v1.6.0+ contract).

Returns rule stats AND the platform event catalog as `available_events`.
LLM consumes both via per-turn skeleton context — that closes the
'inject catalog into system prompt' need without monkey-patching SDK
internals.
"""
from __future__ import annotations

import logging

from app import ext
from api import (
    list_active_rules,
    load_event_catalog_cached,
    load_capability_catalog_cached,
    get_quota,
)
from constants import (
    SKELETON_RULE_LIMIT,
    PROMPT_TRUNCATE_LEN,
    EVENT_DESC_TRUNCATE_LEN,
)
from models import EventCatalog, CapabilityCatalog

log = logging.getLogger("automations")


@ext.skeleton(
    "rules",
    alert=False,
    ttl=300,
    description="User's automation rule stats + platform event catalog (read by LLM each turn).",
)
async def skeleton_refresh_rules(ctx) -> dict:
    user_id   = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")

    rules: list[dict] = []
    try:
        all_rules = await list_active_rules(ctx, tenant_id=tenant_id)
        rules = [r for r in all_rules if r.get("user_id") == user_id]
    except Exception as exc:
        log.warning("skeleton: rule fetch failed: %s", exc, exc_info=True)

    catalog: EventCatalog
    try:
        catalog = await load_event_catalog_cached(ctx)
    except Exception as exc:
        log.warning("skeleton: catalog fetch failed: %s", exc, exc_info=True)
        catalog = EventCatalog()

    capabilities: CapabilityCatalog
    try:
        capabilities = await load_capability_catalog_cached(ctx)
    except Exception as exc:
        log.warning("skeleton: capability fetch failed: %s", exc, exc_info=True)
        capabilities = CapabilityCatalog()

    quota: dict = {}
    try:
        quota = await get_quota(ctx)
    except Exception as exc:
        log.warning("skeleton: quota fetch failed: %s", exc, exc_info=True)

    return {
        "response": {
            "total":   len(rules),
            "active":  sum(1 for r in rules if r.get("status") == "active"),
            "paused":  sum(1 for r in rules if r.get("status") == "paused"),
            "errored": sum(1 for r in rules if r.get("status") == "error"),
            "rules_summary": [
                {
                    "rule_id": r["id"],
                    "prompt":  (r.get("prompt") or "")[:PROMPT_TRUNCATE_LEN],
                    "status":  r.get("status"),
                }
                for r in rules[:SKELETON_RULE_LIMIT]
            ],
            "available_events": [
                {
                    "event_type":  e.event_type,
                    "description": e.description[:EVENT_DESC_TRUNCATE_LEN],
                }
                for e in catalog.entries
            ],
            # Capability inventory (compact, no descriptions to bound tokens):
            # the producer LLM grounds a StructuredAction (app_id, tool, args)
            # against this. The GW gate rejects out-of-scope/unknown tools, so
            # the full catalog is safe to surface. `*` on a param = required.
            "available_tools": [
                {
                    "app_id":   c.app_id,
                    "tool":     c.tool,
                    "type":     c.action_type,
                    "params":   [f"{p}*" for p in c.required_params] + list(c.optional_params),
                }
                for c in capabilities.entries
            ],
            "quota": {
                "cap":       quota.get("cap"),
                "used":      quota.get("used"),
                "remaining": quota.get("remaining"),
                "unlimited": quota.get("unlimited", False),
                "plan":      quota.get("plan"),
            },
        }
    }
