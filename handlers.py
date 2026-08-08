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
from action_text import validate_ssh_action
from constants import ACTION_DESC_TRUNCATE_LEN, PROMPT_TRUNCATE_LEN
from models import (
    AutomationRule,
    AutomationListResponse,
    AutomationActionReceipt,
    BulkActionReceipt,
    BulkRuleParams,
    CreateAutomationParams,
    ListAutomationsParams,
    OwnerStatsParams,
    OwnerStatsResponse,
    RuleDetailsParams,
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


def _cron_of(r: dict) -> str:
    """The cron expression of a scheduled rule ('' when not scheduled)."""
    tf = r.get("trigger_filter") or {}
    return (tf.get("schedule") or "") if isinstance(tf, dict) else ""


def _event_of(r: dict) -> str:
    """The trigger event type of a rule ('' when absent)."""
    tf = r.get("trigger_filter") or {}
    if isinstance(tf, dict):
        return tf.get("event_type") or r.get("event_type") or ""
    return r.get("event_type") or ""


def _action_text(r: dict) -> str:
    """Flatten a rule's action(s) into one searchable string."""
    out: list[str] = []
    for a in (r.get("actions") or []):
        if not isinstance(a, dict):
            out.append(str(a))
            continue
        if a.get("message"):
            out.append(str(a["message"]))
        tool = a.get("tool")
        if tool:
            out.append(f"{a.get('app_id', '')}.{tool}")
        args = a.get("args")
        if isinstance(args, dict) and args:
            out.append(" ".join(f"{k}={v}" for k, v in args.items()))
    return " ".join(out)


def _age_days(created_at: str) -> int:
    """Whole days since the rule was created (-1 when unknown).

    Age answers 'is this a fresh rule or an old one?' -- the question that
    separates a legitimate long-lived automation from a rule that appeared
    minutes ago, which is exactly how the self-replication incident was
    spotted.
    """
    stamp = (created_at or "").strip()
    if not stamp:
        return -1
    try:
        from datetime import datetime, timezone
        norm = stamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(delta.days, 0)
    except Exception:
        return -1


def _iso_date(value: str) -> str:
    """Normalise an ISO timestamp to its date part for range compares."""
    return (value or "")[:10]


def _rule_summary(r: dict) -> dict:
    """Project one gateway rule into the full, nothing-held-back shape.

    Every field the gateway exposes is surfaced -- ownership (``user_id``),
    creation time, the resolved trigger (event + cron), the concrete action,
    the run counters and the live failure state -- plus derived facts the
    agent would otherwise have to recompute on every question:

      * ``is_scheduled`` / ``schedule``  -- cron rule? which expression?
      * ``event_type``                   -- lifted out of trigger_filter
      * ``never_triggered``              -- has it EVER fired?
      * ``is_failing``                   -- does it currently carry a failure?
      * ``success_rate``                 -- successes / triggers (0.0-1.0)
      * ``action_summary``               -- the action flattened to text
      * ``owner_is_caller``              -- filled by the caller-aware layer

    I-EXT-RECORD-FIELD-NAMING-SYMMETRIC: existing keys keep their exact
    names and types so the panels / sidebar / list-item builders that read
    ``rule_id`` / ``prompt`` / ``status`` keep working untouched.
    """
    trigger_filter = r.get("trigger_filter") or {}
    actions = r.get("actions") or []
    triggers = r.get("trigger_count", 0) or 0
    successes = r.get("success_count", 0) or 0
    fails = r.get("fail_count", 0) or 0
    cron = _cron_of(r)
    last_error = r.get("last_error")
    return {
        "rule_id":          r["id"],
        "prompt":           r.get("prompt", ""),
        "status":           r.get("status", "unknown"),
        "trigger_count":    triggers,
        "success_count":    successes,
        "fail_count":       fails,
        "last_error":       last_error,
        "cooldown_seconds": r.get("cooldown_seconds", 0),
        "notify_mode":      r.get("notify_mode", "all"),
        "created_at":       r.get("created_at", ""),
        "user_id":          r.get("user_id", ""),
        "trigger_filter":   trigger_filter,
        "actions":          actions,
        "interpretation":   r.get("interpretation", ""),
        "last_triggered":   r.get("last_triggered", ""),
        # --- derived, additive (never replaces an existing key) ---
        "event_type":       _event_of(r),
        "schedule":         cron,
        "is_scheduled":     bool(cron),
        "never_triggered":  triggers == 0,
        "is_failing":       bool(last_error) or fails > 0,
        "success_rate":     round(successes / triggers, 3) if triggers else 0.0,
        "age_days":         _age_days(r.get("created_at", "")),
        "action_summary":   _action_text(r)[:ACTION_DESC_TRUNCATE_LEN],
    }


def _sort_rules(rules: list[dict], sort: str) -> list[dict]:
    """Order rules for presentation. Default: newest first.

    A stable, explicit order matters for an agent: 'the first rule' must
    mean the same thing twice in a row.
    """
    key = (sort or "newest").strip().lower()
    if key == "oldest":
        return sorted(rules, key=lambda r: r.get("created_at") or "")
    if key == "most_triggered":
        return sorted(rules, key=lambda r: r.get("trigger_count") or 0, reverse=True)
    if key == "most_failed":
        return sorted(rules, key=lambda r: r.get("fail_count") or 0, reverse=True)
    if key == "owner":
        return sorted(rules, key=lambda r: (r.get("user_id") or "", r.get("created_at") or ""))
    return sorted(rules, key=lambda r: r.get("created_at") or "", reverse=True)


def _matches_filters(
    r: dict,
    *,
    status: str = "",
    user_id: str = "",
    event_type: str = "",
    search: str = "",
    scheduled_only: bool = False,
    failing_only: bool = False,
    never_triggered: bool = False,
    created_after: str = "",
    created_before: str = "",
) -> bool:
    """Does one RAW gateway rule satisfy every supplied filter? (AND).

    Shared by ``list_automations`` and ``bulk_automation_action`` so a
    selection previewed by the list is EXACTLY the selection a bulk verb
    acts on -- no drift between what the agent sees and what it changes.
    """
    if status and r.get("status") != status:
        return False
    if user_id:
        owner = r.get("user_id") or ""
        if user_id not in owner:          # full id or fragment
            return False
    if event_type and event_type.lower() not in _event_of(r).lower():
        return False
    if scheduled_only and not _cron_of(r):
        return False
    if failing_only and not (r.get("last_error") or (r.get("fail_count") or 0) > 0):
        return False
    if never_triggered and (r.get("trigger_count") or 0) != 0:
        return False
    created = _iso_date(r.get("created_at", ""))
    if created_after and created and created < _iso_date(created_after):
        return False
    if created_before and created and created > _iso_date(created_before):
        return False
    if search:
        needle = search.lower()
        hay = " ".join([
            str(r.get("prompt", "")),
            str(r.get("interpretation", "")),
            str(r.get("last_error") or ""),
            _action_text(r),
            _event_of(r),
        ]).lower()
        if needle not in hay:
            return False
    return True


# ─── Read ─────────────────────────────────────────────────────────────── #

@chat.function(
    "list_automations",
    action_type="read",
    description=(
        "List automation rules with full detail: who owns each one (user_id), "
        "when it was created, its trigger event and cron schedule, run counters, "
        "success rate and current failure. Filter by owner (admins), status, "
        "event type, free text, creation date range, or narrow to "
        "scheduled/failing/never-triggered rules."
    ),
    data_model=AutomationListResponse,
)
async def fn_list_automations(ctx, params: ListAutomationsParams) -> ActionResult:
    """List automation rules, filtered by any combination of criteria.

    Non-admins only ever see their own rules; the owner filter is ignored
    for them. Admins see every rule in the tenant and may scope by owner.
    """
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

    # Owner scoping is an ADMIN capability: a non-admin is already confined
    # to their own rules, so honouring it for them would be a no-op at best
    # and a confusing empty result at worst.
    owner_filter = params.user_id.strip() if is_admin else ""

    rules = [
        r for r in rules
        if _matches_filters(
            r,
            status=params.status or "",
            user_id=owner_filter,
            event_type=params.event_type.strip(),
            search=params.search.strip(),
            scheduled_only=params.scheduled_only,
            failing_only=params.failing_only,
            never_triggered=params.never_triggered,
            created_after=params.created_after.strip(),
            created_before=params.created_before.strip(),
        )
    ]
    rules = _sort_rules(rules, params.sort)

    # Cap AFTER filtering + sorting, so a limit never silently hides the
    # very rule the operator was filtering for.
    total_matched = len(rules)
    cap = max(1, min(int(params.limit or 200), 500))
    rules = rules[:cap]

    applied = {
        k: v for k, v in {
            "status":          params.status,
            "user_id":         owner_filter,
            "event_type":      params.event_type.strip(),
            "search":          params.search.strip(),
            "scheduled_only":  params.scheduled_only or None,
            "failing_only":    params.failing_only or None,
            "never_triggered": params.never_triggered or None,
            "created_after":   params.created_after.strip(),
            "created_before":  params.created_before.strip(),
            "sort":            params.sort,
        }.items() if v
    }

    items = [_rule_summary(r) for r in rules]
    for it in items:
        it["owner_is_caller"] = it.get("user_id") == user_id
    owners = {it.get("user_id", "") for it in items}

    subject = "System has" if is_admin else "You have"
    detail = []
    if applied:
        detail.append("filtered")
    if is_admin and len(owners) > 1:
        detail.append(f"{len(owners)} owners")
    suffix = f" [{', '.join(detail)}]" if detail else ""
    summary = (
        f"{subject} {len(items)} {params.status or 'automation'} "
        f"rule(s){scope_note}{suffix}"
    )

    # SDL entity-list (NO legacy {"rules": [...]} wrapper): each rule is a
    # canonical AutomationRule entity (id=rule_id, title=prompt, kind via the
    # subclass-name default; _sdl_canon fills the core fields). The kernel reads
    # data["items"] + title to resolve a rule SET and fan out by id. Data conforms
    # to the sdl.EntityList[AutomationRule] contract (x-sdl="entity-list").
    return ActionResult.success(
        data={
            "items":         items,
            "total":         len(items),
            "total_matched": total_matched,
            "truncated":     total_matched > len(items),
            "admin_view":    is_admin,
            "filter":        applied,
        },
        summary=summary,
    )


@chat.function(
    "get_automation_details",
    action_type="read",
    description=(
        "Get EVERY detail of one automation rule: its owner (user_id), when it "
        "was created, the exact trigger event and cron schedule, the resolved "
        "action, cooldown, notification mode, run counters, success rate, last "
        "run and current error. Admins can inspect ANY user's rule by id."
    ),
    id_projection="rule_id",
    data_model=AutomationRule,
)
async def fn_get_automation_details(ctx, params: RuleDetailsParams) -> ActionResult:
    """Fetch full details of one rule.

    Ownership: a normal user may only read their own rule; an admin may
    read ANY rule in the tenant (and the receipt says whose it is).

    Before this, an admin asking about a rule they did not personally
    create got a flat "not found or not yours", which is indistinguishable
    from the rule genuinely not existing -- the exact failure an operator
    hits when auditing someone else's automations.
    """
    try:
        rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("get_automation_details: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rule.")

    user_id  = _user_id(ctx)
    is_admin = await _is_admin(ctx)

    match = next((r for r in rules if r.get("id") == params.rule_id), None)
    if match is None:
        return ActionResult.error(
            f"Rule #{params.rule_id} does not exist in this tenant."
        )

    owner = match.get("user_id", "")
    if owner != user_id and not is_admin:
        # Truthful and non-leaking: distinct from "does not exist", but it
        # reveals nothing about the rule itself.
        return ActionResult.error(
            f"Rule #{params.rule_id} belongs to another user "
            f"-- you can only view your own automations."
        )

    # SDL: return the gateway rule dict AS the AutomationRule entity (NO legacy
    # {"rule": ...} wrapper). _sdl_canon resolves the canonical id from the raw
    # "id" key and mirrors it into rule_id.
    data = dict(match)
    if params.include_schedule_health:
        # Derived facts, so the agent never has to recompute them to answer
        # "is this rule scheduled / healthy / has it ever run?".
        view = _rule_summary(match)
        for k in (
            "event_type", "schedule", "is_scheduled", "never_triggered",
            "is_failing", "success_rate", "action_summary", "age_days",
        ):
            data.setdefault(k, view[k])

    who = "your rule" if owner == user_id else f"owner {owner}"
    return ActionResult.success(
        data=data,
        summary=(
            f"Rule #{params.rule_id} ({who}): "
            f"{(match.get('prompt') or '')[:PROMPT_TRUNCATE_LEN]}"
        ),
    )


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
        # Validate a server action AT AUTHORING TIME. Without this a rule with a
        # missing server or command saves happily and only fails on its first
        # real run -- possibly at 07:00 next morning, far from the cause. The
        # message names the missing field so it can be fixed in one step.
        _ssh_err = validate_ssh_action(
            params.action.app_id, params.action.tool, params.action.args
        )
        if _ssh_err:
            return ActionResult.error(_ssh_err, retryable=True)
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
    _rule, _owner, denied = await _authorize_rule(ctx, params.rule_id)
    if denied is not None:
        return denied

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
        # Same authoring-time server check as create: an edit must never be able
        # to turn a working rule into one that only fails at run time.
        _ssh_err = validate_ssh_action(
            params.action.app_id, params.action.tool, params.action.args
        )
        if _ssh_err:
            return ActionResult.error(_ssh_err, retryable=True)
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


async def _authorize_rule(ctx, rule_id: int) -> tuple[dict | None, str, ActionResult | None]:
    """Resolve a rule and decide whether the caller may MUTATE it.

    Returns ``(rule, owner, None)`` when allowed, or ``(None, "", error)``.

    One place owns this decision so pause / resume / update / delete cannot
    drift apart. The two failure modes are deliberately distinct:
    a rule that does not exist says so, and a rule owned by somebody else
    says THAT -- without leaking any of its content.
    """
    try:
        rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("authorize rule %s: fetch failed: %s", rule_id, exc, exc_info=True)
        # Fail OPEN to the gateway, which enforces ownership server-side too:
        # a transient read failure must not block a legitimate operator.
        return {}, "", None

    # An EMPTY list is not evidence of absence: list_active_rules returns []
    # both when the tenant genuinely has no rules AND when the internal
    # listing endpoint is unavailable (it swallows any non-200 into []).
    # Refusing here would turn a transient gateway hiccup into "your rule
    # does not exist" and block a legitimate edit, so defer to the gateway,
    # which enforces ownership server-side anyway.
    if not rules:
        return {}, "", None

    match = next((r for r in rules if r.get("id") == rule_id), None)
    if match is None:
        return None, "", ActionResult.error(f"Rule #{rule_id} does not exist in this tenant.")

    owner = match.get("user_id", "")
    if owner != _user_id(ctx) and not await _is_admin(ctx):
        return None, "", ActionResult.error(
            f"Rule #{rule_id} belongs to another user -- you can only manage "
            f"your own automations."
        )
    return match, owner, None


async def _apply_status_patch(
    ctx, rule_id: int, *,
    patch: dict, verb: str, extra_data: dict | None = None,
) -> ActionResult:
    rule, owner, denied = await _authorize_rule(ctx, rule_id)
    if denied is not None:
        return denied

    try:
        ok = await patch_rule(ctx, rule_id, patch)
    except Exception as exc:
        log.warning("patch rule %s failed: %s", rule_id, exc, exc_info=True)
        return ActionResult.error(f"Failed to {verb} rule #{rule_id}: {exc}")
    if not ok:
        return ActionResult.error(f"Failed to {verb} rule #{rule_id}.")
    data = {"rule_id": rule_id, "status": patch.get("status", "")}
    if owner:
        data["user_id"] = owner
    if extra_data:
        data.update(extra_data)
    whose = "" if not owner or owner == _user_id(ctx) else f" (owner {owner})"
    return ActionResult.success(data=data, summary=f"Rule #{rule_id} {verb}{whose}")


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
    rule, owner, denied = await _authorize_rule(ctx, params.rule_id)
    if denied is not None:
        return denied

    # Capture what is about to be destroyed so the receipt is a real record
    # of the deletion rather than just an id.
    doomed = _rule_summary(rule) if rule else {}

    try:
        ok = await delete_rule(ctx, params.rule_id)
    except Exception as exc:
        log.warning("delete_automation: HTTP failed: %s", exc, exc_info=True)
        return ActionResult.error(f"Failed to delete rule #{params.rule_id}: {exc}")
    if not ok:
        return ActionResult.error(f"Failed to delete rule #{params.rule_id}.")

    data = {"rule_id": params.rule_id, "deleted": True}
    if owner:
        data["user_id"] = owner
    for k in ("prompt", "event_type", "schedule", "created_at", "trigger_count"):
        if doomed.get(k) not in (None, ""):
            data[f"deleted_{k}"] = doomed[k]

    whose = "" if not owner or owner == _user_id(ctx) else f" (owner {owner})"
    return ActionResult.success(
        data=data,
        summary=f"Rule #{params.rule_id} deleted{whose}",
    )


# ─── Fleet operations (admin oversight + bulk lifecycle) ──────────────── #

@chat.function(
    "automation_owners",
    action_type="read",
    description=(
        "Break automation rules down BY OWNER: how many each user has, how many "
        "are active / paused / failing / never-triggered / scheduled, their total "
        "runs and failures, and when they created their first and last rule. "
        "Answers 'whose automations are these' and 'who is generating load'."
    ),
    data_model=OwnerStatsResponse,
)
async def fn_automation_owners(ctx, params: OwnerStatsParams) -> ActionResult:
    """Aggregate rules per owner (admins see everyone; users see themselves)."""
    try:
        all_rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("automation_owners: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rules.")

    user_id  = _user_id(ctx)
    is_admin = await _is_admin(ctx)
    rules    = all_rules if is_admin else [r for r in all_rules if r.get("user_id") == user_id]

    wanted = params.user_id.strip()
    if wanted and is_admin:
        rules = [r for r in rules if wanted in (r.get("user_id") or "")]

    buckets: dict[str, dict] = {}
    for r in rules:
        view  = _rule_summary(r)
        owner = view["user_id"] or "(unknown)"
        b = buckets.setdefault(owner, {
            "user_id": owner, "total": 0, "active": 0, "paused": 0,
            "failing": 0, "never_triggered": 0, "scheduled": 0,
            "total_runs": 0, "total_failures": 0,
            "first_created": "", "last_created": "", "rule_ids": [],
        })
        b["total"] += 1
        if view["status"] == "active":
            b["active"] += 1
        elif view["status"] == "paused":
            b["paused"] += 1
        if view["is_failing"]:
            b["failing"] += 1
        if view["never_triggered"]:
            b["never_triggered"] += 1
        if view["is_scheduled"]:
            b["scheduled"] += 1
        b["total_runs"]     += view["trigger_count"] or 0
        b["total_failures"] += view["fail_count"] or 0
        b["rule_ids"].append(view["rule_id"])
        created = view["created_at"] or ""
        if created:
            if not b["first_created"] or created < b["first_created"]:
                b["first_created"] = created
            if not b["last_created"] or created > b["last_created"]:
                b["last_created"] = created

    items = sorted(buckets.values(), key=lambda b: b["total"], reverse=True)
    for b in items:
        b["rule_ids"].sort()

    if params.include_rule_ids is False:
        for b in items:
            b.pop("rule_ids", None)

    scope = "across the tenant" if is_admin else "for you"
    return ActionResult.success(
        data={
            "items":        items,
            "total":        len(items),
            "total_owners": len(items),
            "total_rules":  sum(b["total"] for b in items),
            "admin_view":   is_admin,
        },
        summary=(
            f"{len(items)} owner(s), {sum(b['total'] for b in items)} "
            f"automation rule(s) {scope}"
        ),
    )


@chat.function(
    "bulk_automation_action",
    action_type="destructive",
    # A fleet-wide pause/resume/delete is exactly the kind of action that must
    # leave a trace: the emitted event is what makes a bulk sweep auditable
    # afterwards, the same way rule_deleted covers a single delete.
    event="rules_bulk_changed",
    effects=["update:automation", "delete:automation"],
    description=(
        "Pause, resume or DELETE several automation rules in one call. Target "
        "them by explicit rule_ids, or select them with the same filters as "
        "list_automations (owner, status, event, text, never-triggered, "
        "failing). Always run with dry_run=true first to see exactly what "
        "would be affected. Admins may act on other users' rules."
    ),
    data_model=BulkActionReceipt,
)
async def fn_bulk_automation_action(ctx, params: BulkRuleParams) -> ActionResult:
    """Apply one lifecycle operation to a whole set of rules.

    Cleaning up a runaway or abandoned set of rules one id at a time is
    slow and error-prone; this does the selection and the operation in a
    single, auditable step, and refuses to act on an unfiltered set unless
    ids were given explicitly.
    """
    op = (params.operation or "").strip().lower()
    if op not in ("pause", "resume", "delete"):
        return ActionResult.error(
            "operation must be 'pause', 'resume' or 'delete'.", retryable=True,
        )

    try:
        all_rules = await list_active_rules(ctx, tenant_id=_tenant_id(ctx))
    except Exception as exc:
        log.warning("bulk_automation_action: fetch failed: %s", exc, exc_info=True)
        return ActionResult.error("Failed to fetch automation rules.")

    user_id  = _user_id(ctx)
    is_admin = await _is_admin(ctx)
    scoped   = all_rules if is_admin else [r for r in all_rules if r.get("user_id") == user_id]

    if params.rule_ids:
        wanted   = set(params.rule_ids)
        selected = [r for r in scoped if r.get("id") in wanted]
        missing  = sorted(wanted - {r.get("id") for r in selected})
        if missing:
            return ActionResult.error(
                f"These rule(s) do not exist or are not yours: {missing}. "
                f"Nothing was changed."
            )
    else:
        owner_filter = params.user_id.strip() if is_admin else ""
        has_filter = any([
            owner_filter, params.status.strip(), params.event_type.strip(),
            params.search.strip(), params.never_triggered, params.failing_only,
        ])
        if not has_filter:
            # Refusing an unfiltered bulk mutation is the whole safety story.
            return ActionResult.error(
                "Refusing to act on EVERY rule. Pass explicit rule_ids, or at "
                "least one filter (user_id / status / event_type / search / "
                "never_triggered / failing_only).",
                retryable=True,
            )
        selected = [
            r for r in scoped
            if _matches_filters(
                r,
                status=params.status.strip(),
                user_id=owner_filter,
                event_type=params.event_type.strip(),
                search=params.search.strip(),
                never_triggered=params.never_triggered,
                failing_only=params.failing_only,
            )
        ]

    previews = [_rule_summary(r) for r in _sort_rules(selected, "newest")]

    if params.dry_run:
        return ActionResult.success(
            data={
                "operation":  op,
                "selected":   len(previews),
                "succeeded":  [],
                "failed":     [],
                "dry_run":    True,
                "admin_view": is_admin,
                "items":      previews,
            },
            summary=(
                f"DRY RUN — {op} would affect {len(previews)} rule(s). "
                f"Re-run with dry_run=false to apply."
            ),
        )

    succeeded: list[int] = []
    failed: list[dict] = []
    for r in previews:
        rid = r["rule_id"]
        try:
            if op == "delete":
                ok = await delete_rule(ctx, rid)
            elif op == "pause":
                ok = await patch_rule(ctx, rid, {"status": "paused"})
            else:
                ok = await patch_rule(ctx, rid, {"status": "active", "trigger_count": 0})
        except Exception as exc:
            log.warning("bulk %s rule %s failed: %s", op, rid, exc, exc_info=True)
            failed.append({"rule_id": rid, "error": str(exc)})
            continue
        (succeeded if ok else failed).append(
            rid if ok else {"rule_id": rid, "error": "gateway rejected the change"}
        )

    verdict = "all" if not failed else ("partial" if succeeded else "none")
    return ActionResult.success(
        data={
            "operation":  op,
            "selected":   len(previews),
            "succeeded":  succeeded,
            "failed":     failed,
            "dry_run":    False,
            "admin_view": is_admin,
        },
        summary=(
            f"{op}: {len(succeeded)}/{len(previews)} rule(s) succeeded"
            + (f", {len(failed)} failed" if failed else "")
            + f" ({verdict})"
        ),
    )
