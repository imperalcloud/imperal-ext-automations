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
    fetch_user_role_cached,
    get_quota,
)
from constants import ACTION_DESC_TRUNCATE_LEN, PROMPT_TRUNCATE_LEN
from models import (
    AutomationRule,
    AutomationListResponse,
    AutomationActionReceipt,
    CreateAutomationParams,
    ListAutomationsParams,
    RuleIdParams,
    UpdateAutomationParams,
)

log = logging.getLogger("automations")


# ─── ctx accessors (kernel guarantees ctx.user) ───────────────────────── #

def _user_id(ctx) -> str:
    return ctx.user.imperal_id


def _tenant_id(ctx) -> str:
    return getattr(ctx.user, "tenant_id", "default")


async def _is_admin(ctx) -> bool:
    """Authoritative admin check via auth-gw.

    The kernel-side ``ctx.user.role`` is sourced from a Redis cache
    that defaults to ``"user"`` when stale or unset, so first-party
    extensions that need real role have to query auth-gw directly
    (cached per-user via ctx.cache, see api.fetch_user_role_cached).
    """
    return await fetch_user_role_cached(ctx) == "admin"


def _rule_summary(r: dict) -> dict:
    trigger_filter = r.get("trigger_filter") or {}
    actions = r.get("actions") or []
    return {
        "rule_id":          r["id"],
        "prompt":           r.get("prompt", ""),
        "status":           r.get("status", "unknown"),
        "trigger_count":    r.get("trigger_count", 0),
        "success_count":    r.get("success_count", 0),
        "fail_count":       r.get("fail_count", 0),
        "last_error":       r.get("last_error"),
        "cooldown_seconds": r.get("cooldown_seconds", 0),
        "notify_mode":      r.get("notify_mode", "all"),
        "created_at":       r.get("created_at", ""),
        "user_id":          r.get("user_id", ""),
        "trigger_filter":   trigger_filter,
        "actions":          actions,
        "interpretation":   r.get("interpretation", ""),
        "last_triggered":   r.get("last_triggered", ""),
    }


# ─── Read ─────────────────────────────────────────────────────────────── #

@chat.function(
    "list_automations",
    action_type="read",
    description="List all your automation rules with status and execution stats.",
    data_model=AutomationListResponse,
)
async def fn_list_automations(ctx, params: ListAutomationsParams) -> ActionResult:
    """List automation rules. Optional `status` filter narrows by state."""
    try:
        all_rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("list_automations: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rules.")

    user_id  = _user_id(ctx)
    is_admin = await _is_admin(ctx)
    if is_admin:
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
        f"System has {len(rules)} {params.status or 'automation'} rule(s){scope_note}"
        if is_admin
        else f"You have {len(rules)} {params.status or 'automation'} rule(s){scope_note}"
    )

    # SDL entity-list (NO legacy {"rules": [...]} wrapper): each rule is a
    # canonical AutomationRule entity (id=rule_id, title=prompt, kind via the
    # subclass-name default; _sdl_canon fills the core fields). The kernel reads
    # data["items"] + title to resolve a rule SET and fan out by id. Data conforms
    # to the sdl.EntityList[AutomationRule] contract (x-sdl="entity-list").
    return ActionResult.success(
        data={
            "items":      [_rule_summary(r) for r in rules],
            "total":      len(rules),
            "admin_view": is_admin,
            "filter":     {"status": params.status},
        },
        summary=summary,
    )


@chat.function(
    "get_automation_details",
    action_type="read",
    description="Get detailed information about a specific automation rule.",
    id_projection="rule_id",
    data_model=AutomationRule,
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
            # SDL: return the raw gateway rule dict AS the AutomationRule entity
            # (NO legacy {"rule": ...} wrapper). _sdl_canon resolves the canonical
            # id from the raw "id" key and mirrors it into rule_id; the panels read
            # the gateway response directly, not this chat output.
            return ActionResult.success(
                data=r,
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
    data_model=AutomationActionReceipt,
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

    if params.action is not None:
        actions = [{
            "app_id": params.action.app_id,
            "tool":   params.action.tool,
            "args":   params.action.args,
        }]
    else:
        actions = [{"message": params.action_description}]

    body = {
        "user_id":   _user_id(ctx),
        "tenant_id": _tenant_id(ctx),
        "prompt":    f"When {params.event_type}: {params.action_description}",
        "trigger_filter": {
            "event_type": params.event_type,
            **({"schedule": params.schedule} if params.schedule else {}),
        },
        "actions":          actions,
        "interpretation":   params.action_description[:ACTION_DESC_TRUNCATE_LEN],
        "cooldown_seconds": params.cooldown_seconds,
        "notify_mode":      params.notify_mode,
    }

    try:
        rule = await create_rule(ctx, body=body)
    except Exception as exc:
        log.warning("create_automation: HTTP failed: %s", exc, exc_info=True)
        return ActionResult.error(f"Failed to create automation: {exc}")

    if isinstance(rule, dict) and rule.get("error") == "quota_exceeded":
        q = rule.get("quota") or {}
        # Emit structured FACTS — narrator owns phrasing (ICNLI).
        # ActionResult.error only accepts a str message; encode facts inline so
        # the kernel narrator can render them in the session language.
        return ActionResult.error(
            f"automation_quota_exceeded: cap={q.get('cap')} used={q.get('used')}"
            f" plan={q.get('plan')} source={q.get('source')}",
            retryable=False,
        )

    if not rule:
        return ActionResult.error("Failed to create automation rule.")

    return ActionResult.success(
        data={"rule_id": rule.get("id"), "rule": rule},
        summary=f"Rule #{rule.get('id')} created: {params.action_description[:PROMPT_TRUNCATE_LEN]}",
    )


@chat.function(
    "update_automation",
    action_type="write",
    event="rule_updated",
    chain_callable=True,
    effects=["update:automation"],
    id_projection="rule_id",
    description=(
        "Edit an existing automation rule in place (prompt/schedule/cooldown/status) "
        "without delete+recreate; preserves rule_id and stats."
    ),
    data_model=AutomationActionReceipt,
)
async def fn_update_automation(ctx, params: UpdateAutomationParams) -> ActionResult:
    """Patch an existing rule. Re-grounds any changed trigger/action before persisting."""
    patch: dict = {}

    if params.event_type is not None or params.schedule is not None:
        catalog = await load_event_catalog_cached(ctx)
        valid = catalog.valid_event_types
        if params.event_type is not None:
            if valid and params.event_type not in valid:
                return ActionResult.error(
                    f"Event '{params.event_type}' not found. Available: {', '.join(sorted(valid))}",
                    retryable=True,
                )
        new_event = params.event_type
        new_sched = params.schedule
        if new_event == "system.scheduled" and not new_sched:
            return ActionResult.error(
                "schedule (cron) is required for system.scheduled.",
                retryable=True,
            )
        if new_sched:
            try:
                from croniter import croniter
                croniter(new_sched)
            except (ValueError, KeyError) as exc:
                return ActionResult.error(f"Invalid cron '{new_sched}': {exc}")
        tf: dict = {}
        if new_event is not None:
            tf["event_type"] = new_event
        if new_sched:
            tf["schedule"] = new_sched
        if tf:
            patch["trigger_filter"] = tf

    if params.action is not None:
        # Structured (grounded) edit — GW PATCH re-grounds it (tool exists,
        # in scope, required args present) before persisting. Mirrors create.
        patch["actions"] = [{
            "app_id": params.action.app_id,
            "tool":   params.action.tool,
            "args":   params.action.args,
        }]
        if params.action_description is not None:
            patch["interpretation"] = params.action_description[:ACTION_DESC_TRUNCATE_LEN]
            patch["prompt"] = params.action_description
    elif params.action_description is not None:
        patch["actions"] = [{"message": params.action_description}]
        patch["interpretation"] = params.action_description[:ACTION_DESC_TRUNCATE_LEN]
        patch["prompt"] = params.action_description

    if params.cooldown_seconds is not None:
        patch["cooldown_seconds"] = params.cooldown_seconds

    if params.status is not None:
        if params.status not in ("active", "paused"):
            return ActionResult.error("status must be 'active' or 'paused'.", retryable=True)
        patch["status"] = params.status

    if params.notify_mode is not None:
        if params.notify_mode not in ("all", "failures", "off"):
            return ActionResult.error("notify_mode must be 'all', 'failures', or 'off'.", retryable=True)
        patch["notify_mode"] = params.notify_mode

    if not patch:
        return ActionResult.error("Nothing to update — provide at least one field.", retryable=True)

    try:
        ok = await patch_rule(ctx, params.rule_id, patch)
    except Exception as exc:
        log.warning("update_automation: HTTP failed: %s", exc, exc_info=True)
        return ActionResult.error(f"Failed to update rule #{params.rule_id}: {exc}")
    if not ok:
        return ActionResult.error(f"Failed to update rule #{params.rule_id}.")

    return ActionResult.success(
        data={"rule_id": params.rule_id, "status": patch.get("status", "")},
        summary=f"Rule #{params.rule_id} updated",
    )


@chat.function(
    "pause_automation",
    action_type="write",
    event="rule_paused",
    chain_callable=True,
    effects=["update:automation"],
    id_projection="rule_id",
    description="Pause an active automation rule temporarily.",
    data_model=AutomationActionReceipt,
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
    data_model=AutomationActionReceipt,
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
    data_model=AutomationActionReceipt,
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
