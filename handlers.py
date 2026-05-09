"""Automations · @chat.function handlers (federal v4.1+ contract).

Every write/destructive carries `effects=`; every entity-targeting
tool carries `id_projection=` for chain-step argument projection
(federal v4.1.2). All HTTP work is delegated to api.py which uses
ctx.http (federal-clean transport) — no raw httpx in this module.
"""
from __future__ import annotations

import logging

from imperal_sdk.chat import ActionResult

from app import chat
from api import (
    list_active_rules,
    create_rule,
    patch_rule,
    delete_rule,
    load_event_catalog_cached,
)
from constants import ACTION_DESC_TRUNCATE_LEN, PROMPT_TRUNCATE_LEN
from models import CreateAutomationParams, ListAutomationsParams, RuleIdParams

log = logging.getLogger("automations")


# ─── ctx accessors (kernel guarantees ctx.user) ───────────────────────── #

def _user_id(ctx) -> str:
    return ctx.user.imperal_id


def _tenant_id(ctx) -> str:
    return getattr(ctx.user, "tenant_id", "default")


def _is_admin(ctx) -> bool:
    return getattr(ctx.user, "role", "") == "admin"


def _rule_summary(r: dict) -> dict:
    return {
        "rule_id":          r["id"],
        "prompt":           r.get("prompt", ""),
        "status":           r.get("status", "unknown"),
        "trigger_count":    r.get("trigger_count", 0),
        "success_count":    r.get("success_count", 0),
        "fail_count":       r.get("fail_count", 0),
        "last_error":       r.get("last_error"),
        "cooldown_seconds": r.get("cooldown_seconds", 0),
        "max_per_hour":     r.get("max_per_hour", 0),
        "created_at":       r.get("created_at", ""),
        "user_id":          r.get("user_id", ""),
    }


# ─── Read ─────────────────────────────────────────────────────────────── #

@chat.function(
    "list_automations",
    action_type="read",
    description="List all your automation rules with status and execution stats.",
)
async def fn_list_automations(ctx, params: ListAutomationsParams) -> ActionResult:
    """List automation rules. Optional `status` filter narrows by state."""
    try:
        all_rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("list_automations: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rules.")

    user_id = _user_id(ctx)
    if _is_admin(ctx):
        rules = all_rules
        for r in rules:
            if r.get("user_id") != user_id:
                r["_owner"] = r.get("user_id", "unknown")
        scope_note = " (admin view)"
    else:
        rules = [r for r in all_rules if r.get("user_id") == user_id]
        scope_note = ""

    if params.status:
        rules = [r for r in rules if r.get("status") == params.status]

    summary = (
        f"You have {len(rules)} {params.status or 'automation'} rule(s){scope_note}"
        if not _is_admin(ctx)
        else f"System has {len(rules)} {params.status or 'automation'} rule(s){scope_note}"
    )

    return ActionResult.success(
        data={
            "rules":      [_rule_summary(r) for r in rules],
            "total":      len(rules),
            "admin_view": _is_admin(ctx),
            "filter":     {"status": params.status},
        },
        summary=summary,
    )


@chat.function(
    "get_automation_details",
    action_type="read",
    description="Get detailed information about a specific automation rule.",
    id_projection="rule_id",
)
async def fn_get_automation_details(ctx, params: RuleIdParams) -> ActionResult:
    """Fetch full details of one rule (must belong to caller)."""
    try:
        rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("get_automation_details: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rule.")

    user_id = _user_id(ctx)
    for r in rules:
        if r.get("id") == params.rule_id and r.get("user_id") == user_id:
            return ActionResult.success(
                data={"rule": r},
                summary=f"Rule #{params.rule_id}: {(r.get('prompt') or '')[:PROMPT_TRUNCATE_LEN]}",
            )
    return ActionResult.error(f"Rule #{params.rule_id} not found or not yours")


# ─── Write ────────────────────────────────────────────────────────────── #

@chat.function(
    "create_automation",
    action_type="write",
    event="rule_created",
    chain_callable=True,
    effects=["create:automation"],
    description="Create a new automation rule from rules.available_events skeleton.",
)
async def fn_create_automation(ctx, params: CreateAutomationParams) -> ActionResult:
    """Create a new automation rule keyed to a platform event."""
    if not params.event_type:
        return ActionResult.error("event_type is required.", retryable=True)
    if not params.action_description:
        return ActionResult.error("action_description is required.", retryable=True)

    catalog = await load_event_catalog_cached(ctx)
    valid = catalog.valid_event_types
    if valid and params.event_type not in valid:
        return ActionResult.error(
            f"Event '{params.event_type}' not found. Available: {', '.join(sorted(valid))}",
            retryable=True,
        )

    if params.event_type == "system.scheduled":
        if not params.schedule:
            return ActionResult.error(
                "schedule (cron) is required for system.scheduled.",
                retryable=True,
            )
        try:
            from croniter import croniter
            croniter(params.schedule)
        except (ValueError, KeyError) as exc:
            return ActionResult.error(f"Invalid cron '{params.schedule}': {exc}")

    body = {
        "user_id":   _user_id(ctx),
        "tenant_id": _tenant_id(ctx),
        "prompt":    f"When {params.event_type}: {params.action_description}",
        "trigger_filter": {
            "event_type": params.event_type,
            **({"schedule": params.schedule} if params.schedule else {}),
        },
        "actions":          [{"message": params.action_description}],
        "interpretation":   params.action_description[:ACTION_DESC_TRUNCATE_LEN],
        "cooldown_seconds": params.cooldown_seconds,
        "max_per_hour":     params.max_per_hour,
    }

    try:
        rule = await create_rule(ctx, body=body)
    except Exception as exc:
        log.warning("create_automation: HTTP failed: %s", exc, exc_info=True)
        return ActionResult.error(f"Failed to create automation: {exc}")

    if not rule:
        return ActionResult.error("Failed to create automation rule.")

    return ActionResult.success(
        data={"rule_id": rule.get("id"), "rule": rule},
        summary=f"Rule #{rule.get('id')} created: {params.action_description[:PROMPT_TRUNCATE_LEN]}",
    )


@chat.function(
    "pause_automation",
    action_type="write",
    event="rule_paused",
    chain_callable=True,
    effects=["update:automation"],
    id_projection="rule_id",
    description="Pause an active automation rule temporarily.",
)
async def fn_pause_automation(ctx, params: RuleIdParams) -> ActionResult:
    """Mark an active rule as paused. Trigger counts are preserved."""
    return await _apply_status_patch(
        ctx, params.rule_id, patch={"status": "paused"}, verb="paused",
    )


@chat.function(
    "resume_automation",
    action_type="write",
    event="rule_resumed",
    chain_callable=True,
    effects=["update:automation"],
    id_projection="rule_id",
    description="Resume a paused automation rule. Resets trigger count.",
)
async def fn_resume_automation(ctx, params: RuleIdParams) -> ActionResult:
    """Reactivate a paused rule and reset its trigger counter."""
    return await _apply_status_patch(
        ctx, params.rule_id,
        patch={"status": "active", "trigger_count": 0},
        verb="resumed",
        extra_data={"trigger_count_reset": True},
    )


async def _apply_status_patch(
    ctx, rule_id: int, *,
    patch: dict, verb: str, extra_data: dict | None = None,
) -> ActionResult:
    try:
        ok = await patch_rule(ctx, rule_id, patch)
    except Exception as exc:
        log.warning("patch rule %s failed: %s", rule_id, exc, exc_info=True)
        return ActionResult.error(f"Failed to {verb} rule #{rule_id}: {exc}")
    if not ok:
        return ActionResult.error(f"Failed to {verb} rule #{rule_id}.")
    data = {"rule_id": rule_id, "status": patch.get("status", "")}
    if extra_data:
        data.update(extra_data)
    return ActionResult.success(data=data, summary=f"Rule #{rule_id} {verb}")


# ─── Destructive ──────────────────────────────────────────────────────── #

@chat.function(
    "delete_automation",
    action_type="destructive",
    event="rule_deleted",
    chain_callable=True,
    effects=["delete:automation"],
    id_projection="rule_id",
    description="Permanently delete an automation rule.",
)
async def fn_delete_automation(ctx, params: RuleIdParams) -> ActionResult:
    """Permanently delete a rule. Cannot be undone."""
    try:
        ok = await delete_rule(ctx, params.rule_id)
    except Exception as exc:
        log.warning("delete_automation: HTTP failed: %s", exc, exc_info=True)
        return ActionResult.error(f"Failed to delete rule #{params.rule_id}: {exc}")
    if not ok:
        return ActionResult.error(f"Failed to delete rule #{params.rule_id}.")
    return ActionResult.success(
        data={"rule_id": params.rule_id, "deleted": True},
        summary=f"Rule #{params.rule_id} deleted",
    )
