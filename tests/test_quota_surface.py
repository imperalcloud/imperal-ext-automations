"""TDD · Task 15 — quota 429 propagation + skeleton headroom.

Tests:
  1. create_rule returns {'error':'quota_exceeded','quota':{...}} on HTTP 429.
  2. get_quota returns the GW body on HTTP 200.
  3. skeleton_refresh_rules includes a 'quota' block in response.
"""
import pytest
import handlers
import skeleton as skel
import api


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body


class _HTTP:
    def __init__(self, post=None, get=None):
        self._post = post
        self._get = get

    async def post(self, *a, **k):
        return self._post

    async def get(self, *a, **k):
        return self._get


class _User:
    imperal_id = "imp_u_extq"
    tenant_id = "default"


class _Ctx:
    def __init__(self, http):
        self.http = http
        self.user = _User()


@pytest.mark.asyncio
async def test_create_rule_propagates_quota_429():
    ctx = _Ctx(_HTTP(post=_Resp(429, {"detail": {"error_code": "AUTOMATION_QUOTA_EXCEEDED", "quota": {"cap": 3, "used": 3, "plan": "free", "source": "plan"}}})))
    out = await api.create_rule(ctx, body={"user_id": "x"})
    assert out == {"error": "quota_exceeded", "quota": {"cap": 3, "used": 3, "plan": "free", "source": "plan"}}


@pytest.mark.asyncio
async def test_get_quota_returns_body():
    ctx = _Ctx(_HTTP(get=_Resp(200, {"cap": 15, "used": 13, "remaining": 2, "unlimited": False, "plan": "starter"})))
    q = await api.get_quota(ctx)
    assert q["remaining"] == 2 and q["cap"] == 15


@pytest.mark.asyncio
async def test_skeleton_includes_quota_block(monkeypatch):
    ctx = _Ctx(_HTTP())

    async def _rules(_ctx, *, tenant_id):
        return [{"id": 1, "user_id": "imp_u_extq", "status": "active", "prompt": "hello"}]

    async def _cat(_ctx):
        from models import EventCatalog
        return EventCatalog()

    async def _quota(_ctx):
        return {"cap": 15, "used": 13, "remaining": 2, "unlimited": False, "plan": "starter"}

    monkeypatch.setattr(skel, "list_active_rules", _rules)
    monkeypatch.setattr(skel, "load_event_catalog_cached", _cat)
    monkeypatch.setattr(skel, "get_quota", _quota)
    out = await skel.skeleton_refresh_rules(ctx)
    q = out["response"]["quota"]
    assert q["cap"] == 15 and q["used"] == 13 and q["remaining"] == 2
