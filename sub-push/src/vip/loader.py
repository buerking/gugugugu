"""从 big 的 subscriptions.json 展开为 SearchJob（delivery=vip）。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from src import settings
from src.subscriptions_index import SearchJob


logger = logging.getLogger(__name__)


def _vip_cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "VIP", None) or {})


def _subscriptions_path() -> str:
    cfg = _vip_cfg()
    raw = str(cfg.get("subscriptions_path") or "").strip()
    if raw:
        return raw if os.path.isabs(raw) else os.path.join(settings.APP_ROOT, raw)
    big_root = str(cfg.get("big_root") or "").strip()
    if big_root:
        return os.path.join(big_root, "config", "subscriptions.json")
    return ""


def _keywords_of(sub: Dict[str, Any]) -> List[str]:
    raw = sub.get("keywords")
    out: List[str] = []
    seen = set()
    if isinstance(raw, list):
        for k in raw:
            s = str(k or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    if out:
        return out
    one = str(sub.get("keyword") or "").strip()
    return [one] if one else []


def load_vip_jobs(
    *, default_sources: Optional[List[str]] = None
) -> List[SearchJob]:
    cfg = _vip_cfg()
    if not cfg.get("enabled", True):
        return []
    path = _subscriptions_path()
    if not path or not os.path.exists(path):
        logger.warning("VIP 订阅文件不存在，跳过: %s", path or "(未配置)")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.error("读取 VIP 订阅失败: %s", exc)
        return []
    if not isinstance(data, list):
        return []

    default_sources = default_sources or ["mercari", "paypay", "yahoo_auction"]
    jobs: List[SearchJob] = []
    for sub in data:
        if not isinstance(sub, dict) or not sub.get("enabled", True):
            continue
        uid = str(sub.get("sub_user_id") or "").strip() or "vip"
        sources = [
            str(x).strip().lower()
            for x in (sub.get("sources") or default_sources)
            if str(x).strip()
        ]
        if not sources:
            sources = list(default_sources)
        # yahoo 别名
        sources = ["yahoo_auction" if s == "yahoo" else s for s in sources]
        for kw in _keywords_of(sub):
            sid = f"vip:{uid}:{kw}"
            jobs.append(
                SearchJob(
                    job_type="keyword",
                    subscription_id=sid,
                    keyword=kw,
                    sources=sources,
                    price_min=sub.get("price_min") or 0,
                    price_max=sub.get("price_max") or 0,
                    created_time=sub.get("created_time") or 0,
                    web_id=sources[0] if len(sources) == 1 else "multi",
                    delivery="vip",
                    reference_key=str(sub.get("reference_key") or "default"),
                    customer_name=str(sub.get("customer_name") or uid),
                    sub_user_id=uid,
                    user_email=str(sub.get("user_email") or ""),
                    notify_channels=[
                        str(c).strip()
                        for c in (sub.get("notify_channels") or ["email"])
                        if str(c).strip()
                    ],
                    raw=sub,
                )
            )
    logger.debug("VIP jobs=%s path=%s", len(jobs), path)
    return jobs
