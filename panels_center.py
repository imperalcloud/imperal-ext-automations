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
from action_text import describe_actions
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
    center_overlay=True,
    refresh="on_event:rule_created,rule_paused,rule_resumed,rule_deleted,rule_updated",
)
async def automations_workshop(ctx, **kwargs):
    """Center workshop: rule creator/editor + per-rule outcomes dashboard."""
    user_id = ctx.user.imperal_id
    tenant_id = getattr(ctx.user, "tenant_id", "default")
    is_admin = await fetch_user_role_cached(ctx) == "admin"
    edit_rule_id = _parse_rule_id(kwargs.get("edit_rule_id"))

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
    editing_rule = next((r for r in rules if r.get("id") == edit_rule_id), None) if edit_rule_id else None

    return ui.Stack(
        children=[
            _stats_strip(rules),
            _editor_card(catalog, editing_rule),
            ui.Divider(f"Recent outcomes ({len(rules)})"),
            _outcomes_table(rules),
            ui.Divider("Tips"),
            _tips(),
        ],
        gap=3,
        className="p-4 max-w-3xl mx-auto",
    )


def _parse_rule_id(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _stats_strip(rules: list[dict]):
    total = len(rules)
    triggers = sum(r.get("trigger_count", 0) for r in rules)
    successes = sum(r.get("success_count", 0) for r in rules)
    failures = sum(r.get("fail_count", 0) for r in rules)
    success_rate = f"{int(100 * successes / triggers)}%" if triggers else "—"
    return ui.Stats(children=[
        ui.Stat(label="Rules", value=str(total), icon="Workflow"),
        ui.Stat(label="Triggers", value=str(triggers), icon="Zap"),
        ui.Stat(label="Successes", value=str(successes), icon="Check", color="green"),
        ui.Stat(label="Failures", value=str(failures), icon="X", color="red"),
        ui.Stat(label="Success rate", value=success_rate, icon="TrendingUp"),
    ])


def _event_options(catalog):
    if catalog and catalog.entries:
        return [
            {
                "value": e.event_type,
                "label": f"{e.event_type} — {e.description[:EVENT_DESC_TRUNCATE_LEN]}".rstrip(" —"),
            }
            for e in catalog.entries
        ]
    return [
        {"value": "system.scheduled", "label": "system.scheduled — cron timer"},
        {"value": "email.received", "label": "email.received — every incoming mail"},
        {"value": "notes.created", "label": "notes.created — when a note is created"},
    ]


def _editor_card(catalog, editing_rule: dict | None):
    if editing_rule:
        form = _edit_form(catalog, editing_rule)
        title = f"Edit rule #{editing_rule.get('id')}"
        subtitle = "Update the trigger, schedule, prompt, cooldown, or notification mode in place."
        helper = ui.Markdown(
            "This uses `update_automation`, so the rule keeps its history, counters, and existing identity."
        )
    else:
        form = _create_form(catalog)
        title = "New rule"
        subtitle = "Pick a trigger event, describe what should happen, and set the cooldown."
        helper = ui.Markdown(
            "Use the left AI Agents sidebar to manage existing rules inline: pause, resume, open editor, delete, and change notification mode."
        )

    return ui.Card(
        title=title,
        subtitle=subtitle,
        content=ui.Stack([helper, form], gap=2),
    )


def _create_form(catalog):
    form = ui.Form(
        action="create_automation",
        submit_label="Create rule",
        children=[
            ui.Select(param_name="event_type", placeholder="Trigger event…", options=_event_options(catalog)),
            ui.TextArea(
                param_name="action_description",
                placeholder="What should happen when the trigger fires? Plain language.",
                rows=3,
            ),
            ui.Input(param_name="schedule", placeholder="Cron (only for system.scheduled), e.g. '0 9 * * *'"),
            ui.Slider(
                param_name="cooldown_seconds",
                label="Cooldown (s) — min seconds between triggers",
                min=10,
                max=3600,
                value=DEFAULT_COOLDOWN_SECONDS,
                step=10,
            ),
        ],
    )
    form.props["confirm"] = "Create this automation? It will run on its own when the trigger fires."
    return form


def _edit_form(catalog, rule: dict):
    trigger_filter = rule.get("trigger_filter") or {}
    # rule_id rides in via `defaults`, NOT a ui.Hidden child: there is no
    # ui.Hidden primitive in the SDK (see imperal_sdk.ui.Password's docstring
    # and extensions/developer, which carries app_id/name the same way).
    # Referencing it raised AttributeError while BUILDING this form, so
    # "Open Editor" rendered nothing at all -- an empty center panel.
    form = ui.Form(
        action="update_automation",
        submit_label="Save changes",
        defaults={"rule_id": str(rule.get("id", ""))},
        children=[
            ui.Markdown("**Trigger event**"),
            ui.Select(
                param_name="event_type",
                placeholder="Trigger event…",
                options=_event_options(catalog),
                value=trigger_filter.get("event_type", ""),
            ),
            ui.Markdown("**What should happen**"),
            ui.TextArea(
                param_name="action_description",
                placeholder="Describe the action in plain language.",
                rows=4,
                value=_edit_action_description(rule),
            ),
            ui.Markdown("**Schedule (cron)**"),
            ui.Input(
                param_name="schedule",
                placeholder="Only for system.scheduled, e.g. 0 9 * * *",
                value=trigger_filter.get("schedule", ""),
            ),
            ui.Slider(
                param_name="cooldown_seconds",
                label="Cooldown (s)",
                min=10,
                max=3600,
                value=int(rule.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS),
                step=10,
            ),
            ui.Markdown("**Run notifications**"),
            ui.Select(
                param_name="notify_mode",
                options=[
                    {"value": "all", "label": "All runs"},
                    {"value": "failures", "label": "Only failures"},
                    {"value": "off", "label": "Off"},
                ],
                value=rule.get("notify_mode", "all"),
            ),
            ui.Markdown("**Status**"),
            ui.Select(
                param_name="status",
                options=[
                    {"value": "active", "label": "Active"},
                    {"value": "paused", "label": "Paused"},
                ],
                value=rule.get("status", "active"),
            ),
        ],
    )
    form.props["confirm"] = f"Save changes to rule #{rule.get('id')}?"
    return form


def _edit_action_description(rule: dict) -> str:
    prompt = (rule.get("prompt") or "").strip()
    prefix = "When "
    if prompt.startswith(prefix) and ": " in prompt:
        return prompt.split(": ", 1)[1].strip()
    interpretation = (rule.get("interpretation") or "").strip()
    if interpretation:
        return interpretation
    # A STRUCTURED action ({app_id, tool, args}) has no "message" key, so the
    # old `actions[0].get("message")` read rendered an empty editor field for
    # every grounded rule. describe_actions renders both shapes -- and for an
    # SSH rule it shows the exact server + command the owner pre-authorized.
    described = describe_actions(rule.get("actions") or [])
    if described:
        return described
    return prompt


def _outcomes_table(rules: list[dict]):
    if not rules:
        return ui.Empty(
            message="No execution data yet — rules trigger as their events fire.",
            icon="History",
        )
    rows = [
        {
            "rule": (r.get("prompt") or "")[:PROMPT_TRUNCATE_LEN],
            "status": r.get("status", "unknown"),
            "triggers": str(r.get("trigger_count", 0)),
            "ok": str(r.get("success_count", 0)),
            "fail": str(r.get("fail_count", 0)),
            "last_err": (r.get("last_error") or "")[:60],
        }
        for r in rules
    ]
    return ui.DataTable(
        columns=[
            ui.DataColumn(key="rule", label="Rule"),
            ui.DataColumn(key="status", label="Status"),
            ui.DataColumn(key="triggers", label="Runs"),
            ui.DataColumn(key="ok", label="OK"),
            ui.DataColumn(key="fail", label="Fail"),
            ui.DataColumn(key="last_err", label="Last error"),
        ],
        rows=rows,
    )


def _tips():
    return ui.Markdown(
        "**How automations work**\n\n"
        "- A rule fires whenever its trigger event arrives. Pick from the dropdown — the catalog is refreshed every 5 min.\n"
        "- For `system.scheduled` rules, fill the **schedule** field with a cron expression (`0 9 * * *` = every day at 09:00 UTC).\n"
        "- The **cooldown** prevents firing on every event in a burst — good for `email.received` if you only care about the first one a minute.\n"
        "- Existing rules are best managed from the left sidebar: pause/resume, notifications, open editor, delete.\n"
        "- Use the center Workshop editor for safe in-place edits so the rule keeps its stats instead of being recreated.\n"
    )
