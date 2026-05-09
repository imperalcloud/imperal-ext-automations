"""Automations · Sidebar panel (left slot) — rule list with inline management."""
from __future__ import annotations

import logging
from datetime import datetime

from imperal_sdk import ui

from app import ext
from api import list_active_rules, load_event_catalog_cached, fetch_user_role_cached
from constants import (
    OWNER_PREFIX_LEN,
    PROMPT_TRUNCATE_LEN,
    EVENT_DESC_TRUNCATE_LEN,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_PER_HOUR,
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
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted",
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
        children.append(ui.Stack([
            ui.Empty(message="No automation rules yet", icon="Bot"),
            ui.Button(
                "Create your first automation",
                icon="Plus",
                variant="primary",
                on_click=ui.Send(
                    "Create an automation that runs every morning at 9 AM",
                ),
            ),
        ], gap=2))
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

    # Status as first sub chip so the list's searchable=True text-filter
    # finds rules by status — type "paused" / "active" / "error" in the
    # search box to slice the list. Avoids needing a custom filter widget.
    sub = [f"[{status}]", f"Runs: {runs}"]
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


# ─── Center panel: rule editor + dashboard ────────────────────────────── #

@ext.panel(
    "center",
    slot="center",
    title="Automation Workshop",
    icon="Workflow",
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted",
)
async def automations_center(ctx, **kwargs):
    """Center workshop: rule creator form + per-rule outcomes dashboard."""
    user_id   = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")
    is_admin  = await fetch_user_role_cached(ctx) == "admin"

    try:
        catalog = await load_event_catalog_cached(ctx)
    except Exception as exc:
        log.warning("center panel: catalog fetch failed: %s", exc, exc_info=True)
        catalog = None

    try:
        all_rules = await list_active_rules(ctx, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("center panel: rule fetch failed: %s", exc, exc_info=True)
        all_rules = []

    rules = all_rules if is_admin else [r for r in all_rules if r.get("user_id") == user_id]

    # ── Stats strip ─────────────────────────────────────────────────────
    total       = len(rules)
    triggers    = sum(r.get("trigger_count", 0) for r in rules)
    successes   = sum(r.get("success_count", 0) for r in rules)
    failures    = sum(r.get("fail_count", 0) for r in rules)
    success_rate = (
        f"{int(100 * successes / triggers)}%" if triggers else "—"
    )

    stats_strip = ui.Stats(children=[
        ui.Stat(label="Rules",        value=str(total),       icon="Workflow"),
        ui.Stat(label="Triggers",     value=str(triggers),    icon="Zap"),
        ui.Stat(label="Successes",    value=str(successes),   icon="Check", color="green"),
        ui.Stat(label="Failures",     value=str(failures),    icon="X",     color="red"),
        ui.Stat(label="Success rate", value=success_rate,     icon="TrendingUp"),
    ])

    # ── Rule editor form ────────────────────────────────────────────────
    if catalog and catalog.entries:
        event_options = [
            {
                "value": e.event_type,
                "label": f"{e.event_type} — {e.description[:EVENT_DESC_TRUNCATE_LEN]}".rstrip(" —"),
            }
            for e in catalog.entries
        ]
    else:
        event_options = [
            {"value": "system.scheduled", "label": "system.scheduled — cron timer"},
            {"value": "email.received",   "label": "email.received — every incoming mail"},
            {"value": "notes.created",    "label": "notes.created — when a note is created"},
        ]

    editor = ui.Card(
        title="New rule",
        subtitle="Pick a trigger event, describe what should happen, set safety caps.",
        children=[
            ui.Form(
                action="create_automation",
                submit_label="Create rule",
                children=[
                    ui.Select(
                        param_name="event_type",
                        placeholder="Trigger event…",
                        options=event_options,
                    ),
                    ui.TextArea(
                        param_name="action_description",
                        placeholder="What should happen when the trigger fires? Plain language.",
                        rows=3,
                    ),
                    ui.Input(
                        param_name="schedule",
                        placeholder="Cron (only for system.scheduled), e.g. '0 9 * * *'",
                    ),
                    ui.Slider(
                        param_name="cooldown_seconds",
                        label=f"Cooldown (s) — min seconds between triggers",
                        min=10, max=3600, value=DEFAULT_COOLDOWN_SECONDS, step=10,
                    ),
                    ui.Slider(
                        param_name="max_per_hour",
                        label=f"Max triggers per hour",
                        min=1, max=200, value=DEFAULT_MAX_PER_HOUR, step=1,
                    ),
                ],
            ),
        ],
    )

    # ── Per-rule outcomes table ─────────────────────────────────────────
    if rules:
        outcomes_rows = [
            {
                "rule":     (r.get("prompt") or "")[:PROMPT_TRUNCATE_LEN],
                "status":   r.get("status", "unknown"),
                "triggers": str(r.get("trigger_count", 0)),
                "ok":       str(r.get("success_count", 0)),
                "fail":     str(r.get("fail_count", 0)),
                "last_err": (r.get("last_error") or "")[:60],
            }
            for r in rules
        ]
        outcomes = ui.DataTable(
            columns=[
                ui.DataColumn(key="rule",     label="Rule"),
                ui.DataColumn(key="status",   label="Status"),
                ui.DataColumn(key="triggers", label="Runs"),
                ui.DataColumn(key="ok",       label="OK"),
                ui.DataColumn(key="fail",     label="Fail"),
                ui.DataColumn(key="last_err", label="Last error"),
            ],
            rows=outcomes_rows,
        )
    else:
        outcomes = ui.Empty(
            message="No execution data yet — rules trigger as their events fire.",
            icon="History",
        )

    # ── Tips ────────────────────────────────────────────────────────────
    tips = ui.Markdown(
        "**How automations work**\n\n"
        "- A rule fires whenever its trigger event arrives. "
        "Pick from the dropdown — the catalog is refreshed every 5 min.\n"
        "- For `system.scheduled` rules, fill the **schedule** field "
        "with a cron expression (`'0 9 * * *'` = every day at 09:00 UTC).\n"
        "- The **cooldown** prevents firing on every event in a burst — "
        "good for `email.received` if you only care about the first one a minute.\n"
        "- The **max-per-hour** is the hard safety cap; even a misbehaving "
        "trigger can't blow past it.\n"
    )

    return ui.Stack(
        children=[
            stats_strip,
            editor,
            ui.Divider(f"Recent outcomes ({total})"),
            outcomes,
            ui.Divider("Tips"),
            tips,
        ],
        gap=3,
        className="p-4 max-w-3xl mx-auto",
    )
