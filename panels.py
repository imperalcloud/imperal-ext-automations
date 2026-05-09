"""Automations · Sidebar panel (left slot) — rule list with inline management."""
from __future__ import annotations

import logging
from datetime import datetime

from imperal_sdk import ui

from app import ext
from api import list_active_rules
from constants import OWNER_PREFIX_LEN, PROMPT_TRUNCATE_LEN

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
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted",
)
async def automations_sidebar(ctx, **kwargs):
    user_id   = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")
    is_admin  = getattr(ctx.user, "role", "") == "admin"

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
        children.append(ui.Empty(
            message="No automation rules yet",
            icon="Bot",
            action=ui.Send("Create an automation that checks my email every morning"),
        ))
    else:
        items = [_rule_list_item(r, is_admin=is_admin, viewer_id=user_id) for r in rules]
        children.append(ui.Divider(f"Rules ({total})"))
        children.append(ui.List(items=items, searchable=True))

    return ui.Stack(children=children, gap=2, className="min-h-full")


def _rule_list_item(rule: dict, *, is_admin: bool, viewer_id: str):
    rid     = rule.get("id", 0)
    status  = rule.get("status", "unknown")
    prompt  = (rule.get("prompt") or "No description")[:PROMPT_TRUNCATE_LEN]
    runs    = rule.get("trigger_count", 0)
    last    = _format_date(rule.get("last_triggered", ""))
    created = _format_date(rule.get("created_at", ""))

    badge_color = (
        "green"  if status == "active" else
        "yellow" if status == "paused" else
        "red"
    )

    sub = [f"Runs: {runs}"]
    if last:
        sub.append(f"Last: {last}")
    if created:
        sub.append(f"Created: {created}")
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
            ui.Stack([
                ui.Button(
                    toggle_label, icon=toggle_icon,
                    variant="outline", size="sm",
                    on_click=ui.Call(toggle_fn, rule_id=rid),
                ),
                ui.Button(
                    "Delete", icon="Trash2",
                    variant="destructive", size="sm",
                    on_click=ui.Call("delete_automation", rule_id=rid),
                ),
            ], direction="horizontal"),
            ui.KeyValue([
                {"key": "ID",        "value": str(rid)},
                {"key": "Cooldown",  "value": f"{rule.get('cooldown_seconds', 60)}s"},
                {"key": "Max/hour",  "value": str(rule.get('max_per_hour', 10))},
                {"key": "Successes", "value": str(rule.get('success_count', 0))},
                {"key": "Failures",  "value": str(rule.get('fail_count', 0))},
            ], columns=2),
        ],
        actions=[{
            "icon":     toggle_icon,
            "on_click": ui.Call(toggle_fn, rule_id=rid),
            "label":    toggle_label,
        }],
    )
