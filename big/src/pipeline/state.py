"""已推送/已见商品去重记录。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Set

from src import settings


def load_seen() -> Dict[str, Any]:
    path = settings.SEEN_STATE_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_seen(data: Dict[str, Any]) -> None:
    path = settings.SEEN_STATE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _bucket(seen_map: Dict[str, Any], sub_key: str) -> Dict[str, Any]:
    """兼容旧格式 list[str] → 新格式 {ids, meta, updated_at}。"""
    raw = seen_map.get(sub_key)
    if raw is None:
        bucket: Dict[str, Any] = {"ids": [], "meta": {}, "updated_at": 0}
        seen_map[sub_key] = bucket
        return bucket
    if isinstance(raw, list):
        bucket = {
            "ids": [str(x) for x in raw],
            "meta": {},
            "updated_at": int(time.time()),
        }
        seen_map[sub_key] = bucket
        return bucket
    if isinstance(raw, dict):
        raw.setdefault("ids", [])
        raw.setdefault("meta", {})
        return raw
    bucket = {"ids": [], "meta": {}, "updated_at": 0}
    seen_map[sub_key] = bucket
    return bucket


def known_ids(sub_key: str, seen_map: Dict[str, Any]) -> Set[str]:
    return set(_bucket(seen_map, sub_key).get("ids") or [])


def filter_unseen(sub_key: str, items: list, *, seen_map: Dict[str, Any]) -> list:
    known = known_ids(sub_key, seen_map)
    fresh = []
    for it in items:
        sid = f"{it.get('source_site')}:{it.get('id')}"
        if sid in known:
            continue
        fresh.append(it)
    return fresh


def get_last_success_at(sub_key: str, seen_map: Dict[str, Any]) -> Optional[int]:
    """上次该订阅搜索全部成功返回的时间戳；无则 None。"""
    bucket = _bucket(seen_map, sub_key)
    raw = bucket.get("last_success_at")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except Exception:
        return None


def set_last_success_at(sub_key: str, ts: int, *, seen_map: Dict[str, Any]) -> None:
    """仅在搜索成功时推进水位；网络失败轮次不要调用。"""
    bucket = _bucket(seen_map, sub_key)
    bucket["last_success_at"] = int(ts)
    seen_map[sub_key] = bucket


def mark_seen(
    sub_key: str,
    items: list,
    *,
    seen_map: Dict[str, Any],
    pushed_ids: Optional[Set[str]] = None,
    keep_last: int = 8000,
) -> None:
    """
    记录已见商品，避免重复推送。
    pushed_ids：本轮实际推送的 id（site:id），便于排查。
    """
    bucket = _bucket(seen_map, sub_key)
    known = list(bucket.get("ids") or [])
    existing = set(known)
    meta: Dict[str, Any] = dict(bucket.get("meta") or {})
    now = int(time.time())
    pushed_ids = pushed_ids or set()

    for it in items:
        sid = f"{it.get('source_site')}:{it.get('id')}"
        if sid not in existing:
            known.append(sid)
            existing.add(sid)
        prev = meta.get(sid) or {}
        meta[sid] = {
            "name": str(it.get("name") or "")[:120],
            "price": it.get("price"),
            "source_site": it.get("source_site"),
            "item_url": it.get("item_url"),
            "seen_at": now,
            "pushed": sid in pushed_ids or bool(prev.get("pushed")),
        }

    if len(known) > keep_last:
        known = known[-keep_last:]
    keep_set = set(known)
    meta = {k: v for k, v in meta.items() if k in keep_set}

    bucket["ids"] = known
    bucket["meta"] = meta
    bucket["updated_at"] = now
    seen_map[sub_key] = bucket
