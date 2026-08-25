"""One honest answer to 'is this rule working RIGHT NOW?'.

Why this module exists — two bugs that were the SAME bug seen from two ends:

    * The panel (rule 999778) painted a calm green "healthy" on a rule that
      had been failing its real work every hour for 15 hours, because a
      `partial` run increments success_count and leaves fail_count untouched.
    * The chat view (rule 999997) reported `is_failing: true` on a rule whose
      last 300 runs were clean, because `fail_count > 0` is a LIFETIME
      counter: one failure back in July marks a rule broken forever.

Both come from judging a rule by its lifetime counters instead of by its
most RECENT outcome. The panel already grew the correct logic; this module is
that same logic lifted to where both callers can share it, so the two surfaces
can never drift apart again and there is exactly ONE definition of "failing".

`health_of` keeps its (label, colour) shape for the panel. `is_failing` is the
boolean projection of it for the chat/tool layer.
"""
from __future__ import annotations

# Labels that mean "this rule is NOT doing its job right now".
_BAD_LABELS = ("error", "failing", "unstable")


def health_of(rule: dict) -> tuple:
    """Reduce a rule to ONE honest health signal: (label, colour).

    Status alone made a rule that was 'active' but had failed its last 40
    runs look identical to one working perfectly. What a user actually needs
    to know at a glance is 'is this thing WORKING', which is status AND the
    recent outcome together.
    """
    status = rule.get("status", "unknown")
    try:
        ok = int(rule.get("success_count") or 0)
        bad = int(rule.get("fail_count") or 0)
    except (TypeError, ValueError):
        ok = bad = 0
    if status == "error":
        return ("error", "red")
    if status == "paused":
        return ("paused", "yellow")
    if bad and not ok:
        return ("failing", "red")

    # PARTIAL runs are the trap. A run where step 1 dies and step 2 succeeds
    # finishes as "partial", which increments success_count and leaves
    # fail_count AND last_failure_at untouched -- only last_error is written.
    # Judging by the counters alone therefore painted rule 999778 a calm green
    # "healthy" while it had been failing its real work every hour for 15
    # hours. So a lingering last_error counts as evidence on its own.
    #
    # It is only treated as STALE (and ignored) when the record actually
    # proves recovery: a logged failure with a later success after it. When
    # there is no last_failure_at to compare against -- the partial case --
    # nothing proves recovery, so the rule is reported unstable rather than
    # flattered.
    err = (rule.get("last_error") or "").strip()
    if err:
        fail_at = (rule.get("last_failure_at") or "").strip() if isinstance(
            rule.get("last_failure_at"), str) else ""
        succ_at = (rule.get("last_success_at") or "").strip() if isinstance(
            rule.get("last_success_at"), str) else ""
        recovered = bool(fail_at and succ_at and succ_at > fail_at)
        if not recovered:
            return ("unstable", "yellow")
    if ok:
        return ("healthy", "green")
    return ("not run yet", "blue")


def is_failing(rule: dict) -> bool:
    """Does this rule carry a CURRENT failure?

    The old expression was `bool(last_error) or fail_count > 0`, where
    fail_count is a LIFETIME counter -- so rule 999997, with 366 successes,
    6 lifetime failures and its most recent run green, was reported as
    failing. A rule that failed once in July is not a failing rule today.

    Recovery is proven the same way the panel proves it, because it is the
    same function: a later success after the last logged failure clears the
    signal; an unexplained lingering error does not.
    """
    return health_of(rule)[0] in _BAD_LABELS
