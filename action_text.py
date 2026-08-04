"""Automations · one human-readable rendering of a stored rule action.

A rule stores its action in ONE of two shapes:

    {"message": "email me the daily summary"}          # free text (LLM planner)
    {"app_id": "notes", "tool": "create_note", "args": {...}}   # structured

Before this module every surface guessed on its own, and each guess read
``action["message"]`` -- which a STRUCTURED action does not have. A grounded
rule therefore rendered as an empty string in the sidebar and in the editor:
the user could not see what their own rule actually does. For an SSH rule that
is not cosmetic, it is a trust problem -- the stored command IS the
pre-authorization, so it must be readable wherever the rule is shown.

`describe_action` is the single answer to "what does this rule do?", and
`describe_actions` covers a multi-step rule. Both are pure, never raise, and
always return something a human can read.
"""
from __future__ import annotations

# The SSH namespace is a platform constant (kernel: EXTERNAL_NS), not an
# installed extension, so it is spelled out here rather than discovered.
SSH_APP_ID = "conn-ssh"

# Per-tool phrasing for the SSH family: the arg that carries the real intent,
# and how to say it in one line. Anything not listed falls back to the generic
# "<tool> on <server>" form -- new tools degrade gracefully, never crash.
_SSH_PHRASING: dict[str, tuple[str, str]] = {
    "run_command":  ("command", "run `{value}` on {target}"),
    "read_file":    ("path",    "read {value} on {target}"),
    "write_file":   ("path",    "write {value} on {target}"),
    "edit_file":    ("path",    "edit {value} on {target}"),
    "grep":         ("pattern", "search for `{value}` on {target}"),
    "list_dir":     ("path",    "list {value} on {target}"),
    "test_target":  ("",        "check that {target} is reachable"),
    "list_targets": ("",        "list the connected servers"),
}

_MAX_VALUE_LEN = 120


def _clip(text: str, limit: int = _MAX_VALUE_LEN) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _describe_ssh(tool: str, args: dict) -> str:
    """One line for an SSH action, naming the server and the actual command."""
    target = str(args.get("connection_id") or "").strip() or "a connected server"
    arg_name, template = _SSH_PHRASING.get(tool, ("", ""))
    if not template:
        return f"{tool} on {target}"
    value = _clip(args.get(arg_name, "")) if arg_name else ""
    if arg_name and not value:
        # Guard-rail mirror: the kernel refuses such a step, so say so plainly
        # instead of rendering a half-sentence the user cannot act on.
        return f"{tool} on {target} — incomplete, {arg_name} is missing"
    return template.format(value=value, target=target)


def describe_action(action: dict | None) -> str:
    """Render ONE stored action as a single readable line.

    Free-text actions return their message. Structured actions are described
    from (app_id, tool, args) -- with SSH given its own phrasing so the exact
    command and server are visible, because that pair is what the owner
    pre-authorized.
    """
    if not isinstance(action, dict):
        return ""

    message = str(action.get("message") or "").strip()
    app_id = str(action.get("app_id") or "").strip()
    tool = str(action.get("tool") or "").strip()

    if not app_id or not tool:
        return _clip(message, 200)

    args = action.get("args") if isinstance(action.get("args"), dict) else {}

    if app_id == SSH_APP_ID:
        return _describe_ssh(tool, args or {})

    pretty_tool = tool.replace("_", " ")
    return f"{pretty_tool} ({app_id})"


def describe_actions(actions: list | None) -> str:
    """Render a rule's whole action list -- steps joined in order."""
    if not isinstance(actions, list):
        return ""
    parts = [describe_action(a) for a in actions]
    return " → ".join(p for p in parts if p)


def validate_ssh_action(app_id: str, tool: str, args: dict | None) -> str | None:
    """Authoring-time check for a server (conn-ssh) action. Returns an error
    string to show the user, or None when the action is fine.

    Mirrors the kernel's execution-time guard deliberately: the kernel MUST
    re-check (an extension can never be the security boundary), but catching it
    here means the user hears about a missing server or command while they are
    still creating the rule -- not on its first scheduled run hours later.

    Non-SSH actions are passed through untouched: extension tools have their own
    Pydantic validation on the executing side, and second-guessing them here
    would reject legitimate dynamic args.
    """
    if (app_id or "") != SSH_APP_ID:
        return None

    tool = (tool or "").strip()
    known = set(_SSH_PHRASING) | {"list_targets"}
    if tool not in known:
        return (
            f"'{tool}' is not a server tool. "
            f"Available: {', '.join(sorted(known))}."
        )

    args = args if isinstance(args, dict) else {}
    if tool != "list_targets" and not str(args.get("connection_id") or "").strip():
        return (
            f"Which server should '{tool}' run on? "
            "Add connection_id (the server's name from your Connections)."
        )

    required = {
        "run_command": "command",
        "read_file":   "path",
        "write_file":  "path",
        "edit_file":   "path",
        "grep":        "pattern",
    }
    field = required.get(tool)
    if field and not str(args.get(field) or "").strip():
        return f"'{tool}' needs {field} — add it to the action args."

    return None


def is_ssh_action(action: dict | None) -> bool:
    """True when this stored action drives one of the user's own servers."""
    return isinstance(action, dict) and str(action.get("app_id") or "") == SSH_APP_ID
