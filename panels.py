"""Automations · Sidebar panel (left) — rule list with inline management."""
from __future__ import annotations

import logging
from datetime import datetime

from imperal_sdk import ui

from app import ext, _get_http, _user_id, _tenant_id

log = logging.getLogger("automations")


def _format_date(iso_str: str) -> str:
    """Format ISO date to short human-readable."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %H:%M")
    except Exception:
        return ""


@ext.panel(
    "sidebar", slot="left", title="AI Agents", icon="Bot",
    default_width=320, min_width=240, max_width=500,
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted",
)
async def automations_sidebar(ctx, **kwargs):
    """Automations sidebar — stats + rule list with inline management."""
    uid = _user_id(ctx)
    tid = _tenant_id(ctx)

    # Fetch rules from Auth GW
    try:
        r = await _get_http().get("/v1/automations/internal/active",
                                  params={"tenant_id": tid})
        all_rules = r.json() if r.status_code == 200 else []
    except Exception:
        all_rules = []

    is_admin = hasattr(ctx, "user") and ctx.user and ctx.user.role == "admin"
    rules = all_rules if is_admin else [
        r for r in all_rules if r.get("user_id") == uid
    ]

    total = len(rules)
    active = sum(1 for r in rules if r.get("status") == "active")
    paused = sum(1 for r in rules if r.get("status") == "paused")
    errored = sum(1 for r in rules if r.get("status") == "error")

    children: list = []

    # ── Stats (sticky top) ────────────────────────────────────────────
    children.append(ui.Stack([
        ui.Badge(f"{active} active", color="green"),
        ui.Badge(f"{paused} paused", color="yellow"),
        ui.Badge(f"{errored} error", color="red") if errored else None,
        ui.Badge(f"{total} total", color="blue"),
    ], direction="horizontal", wrap=True, sticky=True))

    # Filter out None badges
    children[0].props["children"] = [
        c for c in children[0].props["children"] if c is not None
    ]

    # ── Rule list ─────────────────────────────────────────────────────
    if not rules:
        children.append(ui.Empty(
            message="No automation rules yet",
            icon="Bot",
            action=ui.Send("Create an automation that checks my email every morning"),
        ))
    else:
        items = []
        for rule in rules:
            rid = rule.get("id", 0)
            status = rule.get("status", "unknown")
            prompt = rule.get("prompt", "No description")[:80]
            triggers = rule.get("trigger_count", 0)
            last = _format_date(rule.get("last_triggered", ""))
            created = _format_date(rule.get("created_at", ""))

            # Status badge
            badge_color = (
                "green" if status == "active"
                else "yellow" if status == "paused"
                else "red"
            )

            # Subtitle parts
            sub = [f"Runs: {triggers}"]
            if last:
                sub.append(f"Last: {last}")
            if created:
                sub.append(f"Created: {created}")
            if is_admin and rule.get("user_id") != uid:
                sub.append(f"Owner: {rule.get('user_id', '?')[:16]}")

            # Toggle action (pause/resume)
            toggle_fn = "pause_automation" if status == "active" else "resume_automation"
            toggle_icon = "Pause" if status == "active" else "Play"

            items.append(ui.ListItem(
                id=str(rid),
                title=prompt,
                subtitle=" · ".join(sub),
                badge=ui.Badge(status, color=badge_color),
                expandable=True,
                expanded_content=[
                    ui.Stack([
                        ui.Button(
                            "Pause" if status == "active" else "Resume",
                            icon=toggle_icon,
                            variant="outline", size="sm",
                            on_click=ui.Call(toggle_fn, rule_id=rid),
                        ),
                        ui.Button("Delete", icon="Trash2",
                                  variant="destructive", size="sm",
                                  on_click=ui.Call("delete_automation", rule_id=rid)),
                    ], direction="horizontal"),
                    ui.KeyValue([
                        {"key": "ID", "value": str(rid)},
                        {"key": "Cooldown", "value": f"{rule.get('cooldown_seconds', 60)}s"},
                        {"key": "Max/hour", "value": str(rule.get('max_per_hour', 10))},
                        {"key": "Successes", "value": str(rule.get('success_count', 0))},
                        {"key": "Failures", "value": str(rule.get('fail_count', 0))},
                    ], columns=2),
                ],
                actions=[{
                    "icon": toggle_icon,
                    "on_click": ui.Call(toggle_fn, rule_id=rid),
                    "label": "Pause" if status == "active" else "Resume",
                }],
            ))

        children.append(ui.Divider(f"Rules ({total})"))
        children.append(ui.List(items=items, searchable=True))

    return ui.Stack(children=children, gap=2, className="min-h-full")
