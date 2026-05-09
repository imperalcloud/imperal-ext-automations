"""Automations · CRUD handlers."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import chat, ActionResult, _get_http, _user_id, _tenant_id, _load_event_catalog, _get_valid_event_types


# ─── Models ───────────────────────────────────────────────────────────── #

class CreateAutomationParams(BaseModel):
    """Create a new automation rule."""
    event_type: str         = Field(description="Trigger event (e.g. email.received, notes.created)")
    action_description: str = Field(description="What to do when triggered, in natural language")
    schedule: str           = Field(default="", description="Cron expression (system.scheduled only)")
    cooldown_seconds: int   = Field(default=60, description="Min seconds between triggers")
    max_per_hour: int       = Field(default=10, description="Max triggers per hour")


class RuleIdParams(BaseModel):
    """Target a specific rule."""
    rule_id: int = Field(description="The rule ID")


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function("list_automations", action_type="read",
               description="List all your automation rules with status and execution stats.")
async def fn_list_automations(ctx) -> ActionResult:
    user_id = _user_id(ctx)
    await _load_event_catalog()
    try:
        r = await _get_http().get("/v1/automations/internal/active", params={"tenant_id": _tenant_id(ctx)})
        if r.status_code != 200:
            return ActionResult.error(f"Failed to fetch rules: HTTP {r.status_code}")
        all_rules = r.json()
        is_admin = hasattr(ctx, "user") and ctx.user and ctx.user.role == "admin"
        if is_admin:
            rules_data = all_rules
            for rule in rules_data:
                if rule.get("user_id") != user_id:
                    rule["_owner"] = rule.get("user_id", "unknown")
            summary = f"System has {len(rules_data)} automation rule(s) (admin view)"
        else:
            rules_data = [rule for rule in all_rules if rule.get("user_id") == user_id]
            summary = f"You have {len(rules_data)} automation rule(s)"
        return ActionResult.success(
            data={
                "rules": [
                    {
                        "rule_id":          rule["id"],
                        "prompt":           rule.get("prompt", ""),
                        "status":           rule.get("status", "unknown"),
                        "trigger_count":    rule.get("trigger_count", 0),
                        "success_count":    rule.get("success_count", 0),
                        "fail_count":       rule.get("fail_count", 0),
                        "last_error":       rule.get("last_error"),
                        "cooldown_seconds": rule.get("cooldown_seconds", 0),
                        "max_per_hour":     rule.get("max_per_hour", 0),
                        "created_at":       rule.get("created_at", ""),
                        "user_id":          rule.get("user_id", ""),
                    }
                    for rule in rules_data
                ],
                "total": len(rules_data),
                "admin_view": is_admin,
            },
            summary=summary,
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list automations: {e}")


@chat.function("create_automation", action_type="write", event="rule_created",
               description="Create a new automation rule from AVAILABLE TRIGGER EVENTS.")
async def fn_create_automation(ctx, params: CreateAutomationParams) -> ActionResult:
    if not params.event_type:
        return ActionResult.error("event_type is required.", retryable=True)
    if not params.action_description:
        return ActionResult.error("action_description is required.", retryable=True)
    catalog = await _load_event_catalog()
    valid = _get_valid_event_types(catalog)
    if valid and params.event_type not in valid:
        return ActionResult.error(f"Event '{params.event_type}' not found. Available: {', '.join(sorted(valid))}", retryable=True)
    if params.event_type == "system.scheduled":
        if not params.schedule:
            return ActionResult.error("schedule (cron) is required for system.scheduled.", retryable=True)
        try:
            from croniter import croniter
            croniter(params.schedule)
        except (ValueError, KeyError) as e:
            return ActionResult.error(f"Invalid cron '{params.schedule}': {e}")
    body = {
        "user_id": _user_id(ctx), "tenant_id": _tenant_id(ctx),
        "prompt": f"When {params.event_type}: {params.action_description}",
        "trigger_filter": {"event_type": params.event_type, **({"schedule": params.schedule} if params.schedule else {})},
        "actions": [{"message": params.action_description}],
        "interpretation": params.action_description[:200],
        "cooldown_seconds": params.cooldown_seconds, "max_per_hour": params.max_per_hour,
    }
    try:
        r = await _get_http().post("/v1/automations/internal/create", json=body)
        if r.status_code in (200, 201):
            rule = r.json()
            return ActionResult.success(data={"rule_id": rule.get("id"), "rule": rule},
                                        summary=f"Rule #{rule.get('id')} created: {params.action_description[:80]}")
        return ActionResult.error(f"Failed to create: {r.status_code} {r.text}")
    except Exception as e:
        return ActionResult.error(f"Failed to create automation: {e}")


@chat.function("pause_automation", action_type="write", event="rule_paused",
               description="Pause an active automation rule temporarily.")
async def fn_pause_automation(ctx, params: RuleIdParams) -> ActionResult:
    try:
        r = await _get_http().patch(f"/v1/automations/internal/{params.rule_id}", json={"status": "paused"})
        if r.status_code == 200:
            return ActionResult.success(data={"rule_id": params.rule_id, "status": "paused"}, summary=f"Rule #{params.rule_id} paused")
        return ActionResult.error(f"Failed to pause: HTTP {r.status_code}")
    except Exception as e:
        return ActionResult.error(f"Failed: {e}")


@chat.function("resume_automation", action_type="write", event="rule_resumed",
               description="Resume a paused automation rule. Resets trigger count.")
async def fn_resume_automation(ctx, params: RuleIdParams) -> ActionResult:
    try:
        r = await _get_http().patch(f"/v1/automations/internal/{params.rule_id}", json={"status": "active", "trigger_count": 0})
        if r.status_code == 200:
            return ActionResult.success(data={"rule_id": params.rule_id, "status": "active", "trigger_count_reset": True},
                                        summary=f"Rule #{params.rule_id} resumed")
        return ActionResult.error(f"Failed to resume: HTTP {r.status_code}")
    except Exception as e:
        return ActionResult.error(f"Failed: {e}")


@chat.function("delete_automation", action_type="destructive", event="rule_deleted",
               description="Permanently delete an automation rule.")
async def fn_delete_automation(ctx, params: RuleIdParams) -> ActionResult:
    try:
        r = await _get_http().delete(f"/v1/automations/internal/{params.rule_id}")
        if r.status_code in (200, 204):
            return ActionResult.success(data={"rule_id": params.rule_id, "deleted": True}, summary=f"Rule #{params.rule_id} deleted")
        return ActionResult.error(f"Failed to delete: HTTP {r.status_code}")
    except Exception as e:
        return ActionResult.error(f"Failed: {e}")


@chat.function("get_automation_details", action_type="read",
               description="Get detailed information about a specific automation rule.")
async def fn_get_automation_details(ctx, params: RuleIdParams) -> ActionResult:
    user_id = _user_id(ctx)
    try:
        r = await _get_http().get("/v1/automations/internal/active", params={"tenant_id": _tenant_id(ctx)})
        if r.status_code != 200:
            return ActionResult.error(f"Failed to fetch: HTTP {r.status_code}")
        for rule in r.json():
            if rule.get("id") == params.rule_id and rule.get("user_id") == user_id:
                return ActionResult.success(data={"rule": rule}, summary=f"Rule #{params.rule_id}: {rule.get('prompt', '')[:80]}")
        return ActionResult.error(f"Rule #{params.rule_id} not found or not yours")
    except Exception as e:
        return ActionResult.error(f"Failed: {e}")
