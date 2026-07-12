"""Capability-catalog cache paging (split from api.py, module ceiling).

The whole-platform catalog outgrew the single 64KB cache entry
(I-CACHE-VALUE-SIZE-CAP-64KB); api.load_capability_catalog_cached stores it
as lossless pages built here.
"""
from __future__ import annotations


# Page payload budget — comfortably under the 64KB envelope cap so the
# serialized page + envelope wrapper never trips the SDK size guard.
_CAP_PAGE_MAX_BYTES = 45_000


def _paginate_capabilities(entries: list, max_bytes: int) -> list[list]:
    """Greedy LOSSLESS split of catalog entries into pages whose serialized
    size stays under ``max_bytes``. Always at least one page; a single
    pathological entry larger than the budget still ships alone (the SDK
    guard is the final authority for that page)."""
    pages: list[list] = []
    cur: list = []
    cur_bytes = 0
    for e in entries:
        size = len(e.model_dump_json().encode("utf-8")) + 1
        if cur and cur_bytes + size > max_bytes:
            pages.append(cur)
            cur, cur_bytes = [], 0
        cur.append(e)
        cur_bytes += size
    if cur:
        pages.append(cur)
    return pages or [[]]
