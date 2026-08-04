"""Automations · Sidebar panel (left slot) — rule list with inline management."""
from __future__ import annotations

import logging
from datetime import datetime

from imperal_sdk import ui

from action_text import describe_actions, is_ssh_action

from app import ext
from api import list_active_rules, fetch_user_role_cached
from constants import (
    OWNER_PREFIX_LEN,
    PROMPT_TRUNCATE_LEN,
)

log = logging.getLogger("automations")


def _format_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %H:%M")
    except (ValueError, TypeError):
        return ""


@ext.panel(
    "sidebar",
    slot="left",
    title="AI Agents",
    icon="Bot",
    default_width=320,
    min_width=240,
    max_width=500,
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted,rule_updated",
)
async def automations_sidebar(ctx, **kwargs):
    user_id   = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")
    # Authoritative admin check — kernel ctx.user.role drifts (see api.py).
    is_admin = await fetch_user_role_cached(ctx) == "admin"

    try:
        all_rules = await list_active_rules(ctx, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("panel: rule fetch failed: %s", exc, exc_info=True)
        all_rules = []

    rules = all_rules if is_admin else [r for r in all_rules if r.get("user_id") == user_id]

    total   = len(rules)
    active  = sum(1 for r in rules if r.get("status") == "active")
    paused  = sum(1 for r in rules if r.get("status") == "paused")
    errored = sum(1 for r in rules if r.get("status") == "error")

    children: list = []

    # ── Stats (sticky top) ────────────────────────────────────────────── #
    stats = [
        ui.Badge(f"{active} active", color="green"),
        ui.Badge(f"{paused} paused", color="yellow"),
    ]
    if errored:
        stats.append(ui.Badge(f"{errored} error", color="red"))
    stats.append(ui.Badge(f"{total} total", color="blue"))
    children.append(ui.Stack(stats, direction="horizontal", wrap=True, sticky=True))

    # ── Rule list ─────────────────────────────────────────────────────── #
    if not rules:
        # Explicit Button (don't rely on Empty.action — frontend renders that
        # as a generic 'Try again' label which doesn't match our intent).
        # Opens the Workshop creator via the same __panel__workshop route the
        # editor buttons use. NEVER ui.Send a canned "create an automation…"
        # chat prompt here — that created a meaningless no-action rule that
        # fired every morning into nothing (2026-07-13).
        children.append(ui.Stack([
            ui.Empty(message="No automation rules yet", icon="Bot"),
            ui.Button(
                "Create your first automation",
                icon="Plus",
                variant="primary",
                on_click=ui.Call("__panel__workshop", __center_opened=True),
            ),
        ], gap=2))
    else:
        items = [_rule_list_item(r, is_admin=is_admin, viewer_id=user_id) for r in rules]
        children.append(ui.Divider(f"Rules ({total})"))
        children.append(ui.List(items=items, searchable=True))

    # Auto-trigger center overlay (Workshop) on first sidebar mount.
    # Frontend usePanelDiscovery's isCenterOverlay allowlist routes
    # __panel__workshop to setCenterOverlay → ExtensionPage shifts
    # chat to a 380px right rail → Workshop fills the center.
    root = ui.Stack(children=children, gap=2, className="min-h-full")
    if not kwargs.get("__center_opened"):
        root.props["auto_action"] = ui.Call("__panel__workshop", __center_opened=True)
    return root


def _rule_trigger_summary(rule: dict) -> str:
    trigger_filter = rule.get("trigger_filter") or {}
    event_type = trigger_filter.get("event_type") or "manual"
    schedule = trigger_filter.get("schedule") or ""
    return f"{event_type} · {schedule}" if schedule else event_type


def _rule_action_preview(rule: dict):
    actions = rule.get("actions") or []
    # ONE human-readable line first (describe_actions handles free-text AND
    # structured shapes, and spells out the server + command for an SSH rule --
    # that stored pair IS the owner's pre-authorization, so it must be legible
    # at a glance, not printed as a raw args dict).
    described = describe_actions(actions)
    if actions and isinstance(actions[0], dict):
        action = actions[0]
        if action.get("tool"):
            app_id = action.get("app_id") or "app"
            tool = action.get("tool") or "tool"
            rows = [{"key": "Action", "value": described or f"{app_id}.{tool}"}]
            if is_ssh_action(action):
                args = action.get("args") if isinstance(action.get("args"), dict) else {}
                target = str((args or {}).get("connection_id") or "").strip()
                rows.append({"key": "Server", "value": target or "not set"})
                rows.append({"key": "Runs as", "value": "pre-authorized by this rule"})
            else:
                rows.append({"key": "Tool", "value": f"{app_id}.{tool}"})
            return ui.KeyValue(rows, columns=1)
        if described:
            return ui.Markdown(f"**Action**\n\n{described}")
    interpretation = (rule.get("interpretation") or "").strip()
    if interpretation:
        return ui.Markdown(f"**Action**\n\n{interpretation}")
    return ui.Markdown("**Action**\n\nNo action preview available.")


def _rule_detail_grid(rule: dict):
    last_error = (rule.get("last_error") or "").strip() or "—"
    return ui.KeyValue([
        {"key": "Trigger", "value": _rule_trigger_summary(rule)},
        {"key": "Cooldown", "value": f"{rule.get('cooldown_seconds', 60)}s"},
        {"key": "Notify", "value": rule.get("notify_mode", "all")},
        {"key": "Runs", "value": str(rule.get("trigger_count", 0))},
        {"key": "Successes", "value": str(rule.get("success_count", 0))},
        {"key": "Failures", "value": str(rule.get("fail_count", 0))},
        {"key": "Last triggered", "value": _format_date(rule.get("last_triggered", "")) or "—"},
        {"key": "Last error", "value": last_error[:140]},
    ], columns=2)


def _rule_list_item(rule: dict, *, is_admin: bool, viewer_id: str):
    rid     = rule.get("id", 0)
    status  = rule.get("status", "unknown")
    prompt  = (rule.get("prompt") or "No description")[:PROMPT_TRUNCATE_LEN]
    runs    = rule.get("trigger_count", 0)
    last    = _format_date(rule.get("last_triggered", ""))
    created = _format_date(rule.get("created_at", ""))
    nmode   = rule.get("notify_mode", "all")

    badge_color = (
        "green"  if status == "active" else
        "yellow" if status == "paused" else
        "red"
    )

    # Status as first sub chip so the list's searchable=True text-filter
    # finds rules by status — type "paused" / "active" / "error" in the
    # search box to slice the list. Avoids needing a custom filter widget.
    sub = [f"[{status}]", f"Runs: {runs}"]
    if last:
        sub.append(f"Last: {last}")
    if created:
        sub.append(f"Created: {created}")
    event_type = (rule.get("trigger_filter") or {}).get("event_type", "")
    if event_type:
        sub.append(f"Event: {event_type}")
    if is_admin and rule.get("user_id") != viewer_id:
        sub.append(f"Owner: {rule.get('user_id', '?')[:OWNER_PREFIX_LEN]}")

    toggle_fn    = "pause_automation" if status == "active" else "resume_automation"
    toggle_icon  = "Pause"             if status == "active" else "Play"
    toggle_label = "Pause"             if status == "active" else "Resume"

    return ui.ListItem(
        id=str(rid),
        title=prompt,
        subtitle=" · ".join(sub),
        badge=ui.Badge(status, color=badge_color),
        expandable=True,
        expanded_content=[
            ui.Markdown(f"**Rule #{rid}**\n\n{rule.get('prompt') or 'No description'}"),
            _rule_detail_grid(rule),
            _rule_action_preview(rule),
            ui.Stack([
                ui.Button(
                    toggle_label, icon=toggle_icon,
                    variant="outline", size="sm",
                    on_click=ui.Call(toggle_fn, rule_id=rid),
                ),
                ui.Button(
                    "Open editor", icon="SquarePen",
                    variant="outline", size="sm",
                    on_click=ui.Call("__panel__workshop", edit_rule_id=rid, __center_opened=True),
                ),
                ui.Button(
                    "Delete", icon="Trash2",
                    variant="destructive", size="sm",
                    on_click=ui.Call("delete_automation", rule_id=rid),
                ),
            ], direction="horizontal", wrap=True),
            ui.Divider("Notifications"),
            ui.Stack([
                ui.Button(
                    "All", icon="Bell", size="sm",
                    variant=("primary" if nmode == "all" else "outline"),
                    on_click=ui.Call("update_automation", rule_id=rid, notify_mode="all"),
                ),
                ui.Button(
                    "Failures", icon="BellMinus", size="sm",
                    variant=("primary" if nmode == "failures" else "outline"),
                    on_click=ui.Call("update_automation", rule_id=rid, notify_mode="failures"),
                ),
                ui.Button(
                    "Off", icon="BellOff", size="sm",
                    variant=("primary" if nmode == "off" else "outline"),
                    on_click=ui.Call("update_automation", rule_id=rid, notify_mode="off"),
                ),
            ], direction="horizontal", wrap=True),
            ui.KeyValue([
                {"key": "ID",        "value": str(rid)},
                {"key": "Cooldown",  "value": f"{rule.get('cooldown_seconds', 60)}s"},
                {"key": "Notify",    "value": nmode},
                {"key": "Successes", "value": str(rule.get('success_count', 0))},
                {"key": "Failures",  "value": str(rule.get('fail_count', 0))},
            ], columns=2),
            ui.Markdown(
                "To change the trigger, schedule, or action, open the editor. "
                "Deep edits stay in-place via `update_automation`, so the rule keeps its stats."
            ),
        ],
    )
