"""Automations · 'Automation Workshop' center panel.

Split out of panels.py to keep each file under the 300-LOC ceiling
(workspace rule 6). The compact rule-list sidebar (slot=left) lives
in panels.py; this is the rich editor + dashboard surface (slot=center).

Pattern matches whiteboard/canvas, wp-blogger/editor, sql-db/editor:
descriptive panel_id + slot=center renders as always-on center
content. The center-OVERLAY (modal-style, dismissible) is a separate
hardcoded-allowlist path for editor+note_id, compose, email_viewer
panel_ids — irrelevant here.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext
from api import (
    list_active_rules,
    load_event_catalog_cached,
    fetch_user_role_cached,
)
from constants import (
    PROMPT_TRUNCATE_LEN,
    EVENT_DESC_TRUNCATE_LEN,
    DEFAULT_COOLDOWN_SECONDS,
)

log = logging.getLogger("automations")


@ext.panel(
    "workshop",
    slot="center",
    title="Automation Workshop",
    icon="Workflow",
    center_overlay=True,  # federal v4.1.8 — declarative; replaces hardcoded TS allowlist
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted",
)
async def automations_workshop(ctx, **kwargs):
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

    return ui.Stack(
        children=[
            _stats_strip(rules),
            _editor_card(catalog),
            ui.Divider(f"Recent outcomes ({len(rules)})"),
            _outcomes_table(rules),
            ui.Divider("Tips"),
            _tips(),
        ],
        gap=3,
        className="p-4 max-w-3xl mx-auto",
    )


def _stats_strip(rules: list[dict]):
    total     = len(rules)
    triggers  = sum(r.get("trigger_count", 0) for r in rules)
    successes = sum(r.get("success_count", 0) for r in rules)
    failures  = sum(r.get("fail_count", 0) for r in rules)
    success_rate = f"{int(100 * successes / triggers)}%" if triggers else "—"
    return ui.Stats(children=[
        ui.Stat(label="Rules",        value=str(total),       icon="Workflow"),
        ui.Stat(label="Triggers",     value=str(triggers),    icon="Zap"),
        ui.Stat(label="Successes",    value=str(successes),   icon="Check", color="green"),
        ui.Stat(label="Failures",     value=str(failures),    icon="X",     color="red"),
        ui.Stat(label="Success rate", value=success_rate,     icon="TrendingUp"),
    ])


def _editor_card(catalog):
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

    return ui.Card(
        title="New rule",
        subtitle="Pick a trigger event, describe what should happen, set safety caps.",
        content=ui.Form(
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
                    label="Cooldown (s) — min seconds between triggers",
                    min=10, max=3600, value=DEFAULT_COOLDOWN_SECONDS, step=10,
                ),
            ],
        ),
    )


def _outcomes_table(rules: list[dict]):
    if not rules:
        return ui.Empty(
            message="No execution data yet — rules trigger as their events fire.",
            icon="History",
        )
    rows = [
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
    return ui.DataTable(
        columns=[
            ui.DataColumn(key="rule",     label="Rule"),
            ui.DataColumn(key="status",   label="Status"),
            ui.DataColumn(key="triggers", label="Runs"),
            ui.DataColumn(key="ok",       label="OK"),
            ui.DataColumn(key="fail",     label="Fail"),
            ui.DataColumn(key="last_err", label="Last error"),
        ],
        rows=rows,
    )


def _tips():
    return ui.Markdown(
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
