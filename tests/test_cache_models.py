"""Regression: every Pydantic model read/written via ctx.cache.get_or_fetch
MUST be registered with @ext.cache_model, or the SDK raises
"<Model> is not registered" (I-CACHE-MODEL-REGISTRATION-REQUIRED) at runtime
and the read silently fails.

CapabilityCatalog was missing from cache_models.py while api.load_capability_catalog_cached
reads it -> every skeleton refresh logged "CapabilityCatalog is not registered"
and the skeleton's available_tools fell back to empty (breaking grounding for
create_automation). 2026-06-19.

CapabilityPageIndex repeated the same miss when the paged catalog cache landed
(2026-07-12): pages wrote fine but the :idx write raised -> every read fell back
to a fresh fetch ("capability cache write skipped (serving uncached)"). 2026-07-13.
test_api_cache_calls_use_registered_models scans api.py so the NEXT new model
can't ship unregistered either.
"""
import os
import re

import app  # noqa: F401  (ext instance)
import cache_models  # noqa: F401  (import triggers the registrations)
import models
from models import (
    CapabilityCatalog, CapabilityPageIndex, EventCatalog, UserRoleSnapshot,
)


def test_all_cache_models_registered():
    ext = app.ext
    assert ext._resolve_cache_model_name(EventCatalog) == "event_catalog"
    assert ext._resolve_cache_model_name(UserRoleSnapshot) == "user_role"
    # the ones that were missing (2026-06-19, then 2026-07-13):
    assert ext._resolve_cache_model_name(CapabilityCatalog) == "capability_catalog"
    assert ext._resolve_cache_model_name(CapabilityPageIndex) == "capability_page_index"


def test_api_cache_calls_use_registered_models():
    """Every model class named in a ctx.cache call in api.py must resolve."""
    api_src = open(os.path.join(os.path.dirname(__file__), "..", "api.py")).read()
    # Positional form: ctx.cache.get(key, Model) / .set(key, Model(...));
    # kwarg form: ctx.cache.get_or_fetch(key=..., model=Model, ...).
    # PascalCase only — lowercase means a variable indirection, not a class.
    referenced = set(
        re.findall(r"ctx\.cache\.(?:get|set|get_or_fetch)\(\s*[^,]+,\s*([A-Z]\w+)", api_src)
    ) | set(re.findall(r"\bmodel\s*=\s*([A-Z]\w+)", api_src))
    assert referenced, "expected api.py to contain ctx.cache calls"
    for name in sorted(referenced):
        cls = getattr(models, name, None)
        assert cls is not None, f"{name} used in ctx.cache call but not found in models.py"
        assert app.ext._resolve_cache_model_name(cls) is not None, (
            f"{name} used in ctx.cache call but not registered in cache_models.py "
            "(I-CACHE-MODEL-REGISTRATION-REQUIRED)"
        )
