"""Automations · Skeleton tools."""
from __future__ import annotations

from app import ext, _get_http, _user_id, _tenant_id, _load_event_catalog


# ─── Skeleton ─────────────────────────────────────────────────────────── #

@ext.tool("skeleton_automations_stats")
async def skeleton_stats(ctx, **kwargs) -> dict:
    """Background refresh: count user's active rules for skeleton context."""
    user_id = _user_id(ctx)
    await _load_event_catalog()
    try:
        r = await _get_http().get("/v1/automations/internal/active", params={"tenant_id": _tenant_id(ctx)})
        if r.status_code == 200:
            my = [rule for rule in r.json() if rule.get("user_id") == user_id]
            response = {
                "total": len(my),
                "active": sum(1 for r in my if r.get("status") == "active"),
                "paused": sum(1 for r in my if r.get("status") == "paused"),
                "errored": sum(1 for r in my if r.get("status") == "error"),
                "rules_summary": [
                    {"rule_id": r["id"], "prompt": r.get("prompt", "")[:80], "status": r.get("status")}
                    for r in my[:5]
                ],
            }
            return {"response": response}
    except Exception:
        pass
    return {"response": {"total": 0, "active": 0, "paused": 0, "errored": 0, "rules_summary": []}}
