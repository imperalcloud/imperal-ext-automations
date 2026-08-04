"""Automations · action rendering + authoring-time server-action validation.

Two user-visible promises are locked down here:

1. A rule always SHOWS what it does. The old panel code read
   ``action["message"]``, which a STRUCTURED action has not got, so every
   grounded rule rendered blank. For an SSH rule the stored command IS the
   pre-authorization, so a blank render is a trust bug, not a cosmetic one.

2. A server rule that could never run is refused WHILE BEING WRITTEN, naming
   the missing field -- instead of saving happily and failing on its first
   scheduled run hours later.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/extensions/automations")

from action_text import (  # noqa: E402
    describe_action,
    describe_actions,
    is_ssh_action,
    validate_ssh_action,
)


# ─── rendering ────────────────────────────────────────────────────────────

def test_ssh_run_command_reads_as_a_sentence():
    """The pre-authorized command and its server must both be visible."""
    text = describe_action({
        "app_id": "conn-ssh",
        "tool": "run_command",
        "args": {"connection_id": "nl-node1", "command": "uptime"},
    })
    assert "uptime" in text
    assert "nl-node1" in text


def test_structured_action_is_never_blank():
    """Regression: structured actions have no 'message' key. Any non-empty
    structured action must still render (the old code returned '')."""
    for action in (
        {"app_id": "notes", "tool": "create_note", "args": {"title": "x"}},
        {"app_id": "conn-ssh", "tool": "test_target", "args": {"connection_id": "n1"}},
        {"app_id": "mail", "tool": "star", "args": {}},
    ):
        assert describe_action(action).strip(), f"blank render for {action}"


def test_free_text_action_still_renders():
    """The natural-language shape must keep working unchanged."""
    assert describe_action({"message": "email me the summary"}) == "email me the summary"


def test_incomplete_ssh_action_says_what_is_missing():
    text = describe_action({
        "app_id": "conn-ssh",
        "tool": "run_command",
        "args": {"connection_id": "nl-node1"},
    })
    assert "incomplete" in text.lower()
    assert "command" in text.lower()


def test_long_command_is_truncated_for_display():
    text = describe_action({
        "app_id": "conn-ssh",
        "tool": "run_command",
        "args": {"connection_id": "n1", "command": "x" * 500},
    })
    assert len(text) < 200


def test_multi_step_rule_renders_every_step():
    text = describe_actions([
        {"app_id": "conn-ssh", "tool": "run_command",
         "args": {"connection_id": "nl-node1", "command": "df -h"}},
        {"message": "email me the result"},
    ])
    assert "df -h" in text
    assert "email me the result" in text


def test_render_never_raises_on_junk():
    """Panels call this on whatever is stored; it must never be the thing that
    breaks the rule list."""
    for junk in (None, {}, {"args": None}, {"app_id": "conn-ssh"},
                 {"app_id": "conn-ssh", "tool": "run_command", "args": "not-a-dict"}):
        assert isinstance(describe_action(junk), str)
    assert describe_actions(None) == ""
    assert describe_actions("nonsense") == ""


def test_is_ssh_action_only_matches_the_ssh_namespace():
    assert is_ssh_action({"app_id": "conn-ssh"}) is True
    assert is_ssh_action({"app_id": "notes"}) is False
    assert is_ssh_action(None) is False


# ─── authoring-time validation ────────────────────────────────────────────

def test_complete_run_command_is_accepted():
    assert validate_ssh_action(
        "conn-ssh", "run_command",
        {"connection_id": "nl-node1", "command": "uptime"},
    ) is None


def test_missing_server_is_refused_by_name():
    err = validate_ssh_action("conn-ssh", "run_command", {"command": "uptime"})
    assert err and "connection_id" in err


def test_missing_command_is_refused_by_name():
    err = validate_ssh_action("conn-ssh", "run_command", {"connection_id": "n1"})
    assert err and "command" in err


def test_whitespace_only_command_is_refused():
    """A blank command would reach the broker as a no-op and fail far away."""
    err = validate_ssh_action("conn-ssh", "run_command",
                              {"connection_id": "n1", "command": "   "})
    assert err and "command" in err


def test_invented_ssh_tool_is_refused_and_lists_the_real_ones():
    err = validate_ssh_action("conn-ssh", "rm_rf", {"connection_id": "n1"})
    assert err
    assert "run_command" in err          # the message must be actionable


def test_list_targets_needs_no_server():
    assert validate_ssh_action("conn-ssh", "list_targets", {}) is None


def test_non_ssh_actions_are_passed_through_untouched():
    """Extension tools validate on their own executing side; second-guessing
    them here would reject legitimate dynamic args like {{event.id}}."""
    assert validate_ssh_action("notes", "create_note", {}) is None
    assert validate_ssh_action("mail", "star",
                               {"message_ids": ["{{event.id}}"]}) is None


def test_validation_survives_missing_args():
    assert validate_ssh_action("conn-ssh", "run_command", None) is not None
