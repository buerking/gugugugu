"""
商品字段瘦身：只保留合并 / 去重 / 推送需要的字段，丢掉 raw 等大对象。
"""

from __future__ import annotations

import gc
from typing import Any, Dict, Iterable, List, Optional

from src import settings


# 管道内允许保留的键（其余一律丢弃）
_KEEP_KEYS = (
    "id",
    "name",
    "price",
    "currency",
    "thumbnail",
    "status",
    "item_url",
    "source_site",
    "listing_type",
    "created",
    "updated",
    "matched_keyword",
)


def _mem_cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "MEMORY", None) or {})


def slim_item(item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    单条瘦身；永不保留 raw / raw_detail。
    客户端核心展示字段：标题(name)、价格(price)、封面图(thumbnail) —— 封面 URL 整段保留、不截断。
    """
    if not isinstance(item, dict):
        return {}
    cfg = _mem_cfg()
    title_max = max(40, int(cfg.get("title_max_chars") or 200))
    out: Dict[str, Any] = {}
    for k in _KEEP_KEYS:
        if k not in item:
            continue
        v = item.get(k)
        if k == "name":
            out[k] = str(v or "")[:title_max]
        elif k == "thumbnail":
            # 封面图要给用户端展示，URL 禁止截断
            out[k] = str(v or "").strip()
        elif k == "price":
            try:
                out[k] = int(v or 0)
            except Exception:
                out[k] = 0
        elif k == "item_url":
            out[k] = str(v or "").strip()
        else:
            out[k] = v
    if "id" in item and "id" not in out:
        out["id"] = str(item.get("id") or "")
    if "source_site" in item and "source_site" not in out:
        out["source_site"] = str(item.get("source_site") or "")
    return out


def slim_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [slim_item(it) for it in (items or []) if isinstance(it, dict) and it.get("id")]


def maybe_gc(*, force: bool = False) -> None:
    """主动回收；force 或配置开启时调用 gc.collect。"""
    cfg = _mem_cfg()
    if force or bool(cfg.get("gc_enabled", True)):
        gc.collect()
