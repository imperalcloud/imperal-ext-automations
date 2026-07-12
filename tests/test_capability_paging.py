"""Capability catalog paging + cache fail-soft (live 2026-07-12).

The platform capability catalog outgrew the ONE cache entry it was stored in
(83,460 bytes > the 64KB I-CACHE-VALUE-SIZE-CAP-64KB envelope cap), so
ctx.cache.get_or_fetch raised out of the WRITE and the skeleton fell back to
an EMPTY CapabilityCatalog every turn — the automations brain silently lost
its whole capability map ("skeleton: capability fetch failed" in SigNoz).

Contract now:
  * the catalog is stored as PAGES, each comfortably under the cap;
  * a cache failure (read or write) NEVER blanks the catalog — the fresh
    fetch is the answer, caching is only an optimization.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api
from models import CapabilityCatalog, CapabilityEntry

_CAP = 65536  # the SDK client's envelope cap


def _entry(i: int, fat: bool = True) -> CapabilityEntry:
    pad = [f"param_{i}_{j}_padding_padding" for j in range(40)] if fat else []
    return CapabilityEntry(app_id=f"app{i % 7}", tool=f"tool_{i}",
                           action_type="write" if i % 3 else "read",
                           required_params=pad[:20], optional_params=pad[20:])


class _FakeCache:
    """get/set stub with the REAL size guard semantics of the SDK client."""

    def __init__(self, broken: bool = False):
        self.store: dict[str, str] = {}
        self.broken = broken
        self.set_calls: list[str] = []

    async def get(self, key, model):
        raw = self.store.get(key)
        return model.model_validate_json(raw) if raw is not None else None

    async def set(self, key, value, ttl_seconds=60):
        if self.broken:
            raise RuntimeError("cache down")
        body = json.dumps({"envelope": json.loads(value.model_dump_json()),
                           "ttl_seconds": ttl_seconds}, separators=(",", ":"))
        if len(body.encode()) > _CAP:
            raise ValueError(f"cache value too large: {len(body)} > {_CAP} "
                             "bytes (I-CACHE-VALUE-SIZE-CAP-64KB)")
        self.set_calls.append(key)
        self.store[key] = value.model_dump_json()


class _Ctx:
    def __init__(self, cache):
        self.cache = cache


def test_paginate_keeps_every_entry_under_budget():
    entries = [_entry(i) for i in range(90)]           # far over one page
    pages = api._paginate_capabilities(entries, 45_000)
    assert len(pages) > 1
    assert sum(len(p) for p in pages) == len(entries)  # LOSSLESS
    for p in pages:
        assert len(CapabilityCatalog(entries=p).model_dump_json().encode()) < _CAP


def test_oversized_catalog_loads_full_and_caches_paged(monkeypatch):
    raw = [json.loads(_entry(i).model_dump_json()) for i in range(90)]

    async def _fake_raw():
        return raw

    monkeypatch.setattr(api, "_fetch_capability_catalog_raw", _fake_raw)
    cache = _FakeCache()
    got = asyncio.run(api.load_capability_catalog_cached(_Ctx(cache)))
    assert len(got.entries) == 90                       # FULL catalog, no blanking
    assert any(k.endswith(":idx") for k in cache.set_calls)
    assert sum(1 for k in cache.set_calls if ":p" in k) >= 2   # really paged


def test_warm_pages_served_without_refetch(monkeypatch):
    raw = [json.loads(_entry(i).model_dump_json()) for i in range(90)]
    calls = {"n": 0}

    async def _fake_raw():
        calls["n"] += 1
        return raw

    monkeypatch.setattr(api, "_fetch_capability_catalog_raw", _fake_raw)
    cache = _FakeCache()
    ctx = _Ctx(cache)
    first = asyncio.run(api.load_capability_catalog_cached(ctx))
    second = asyncio.run(api.load_capability_catalog_cached(ctx))
    assert calls["n"] == 1                              # second read = cache hit
    assert len(second.entries) == len(first.entries) == 90


def test_broken_cache_never_blanks_the_catalog(monkeypatch):
    raw = [json.loads(_entry(i).model_dump_json()) for i in range(30)]

    async def _fake_raw():
        return raw

    monkeypatch.setattr(api, "_fetch_capability_catalog_raw", _fake_raw)
    got = asyncio.run(api.load_capability_catalog_cached(_Ctx(_FakeCache(broken=True))))
    assert len(got.entries) == 30                       # fetch wins; cache is optional
