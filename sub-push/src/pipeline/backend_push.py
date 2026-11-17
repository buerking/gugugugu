"""
后端回传客户端（对接 jpkix push-search-subscription-hits）。

按 subscriptionId 聚合 → 6 字段 items → 表单 data+sign 分批 POST。
本地联调仍可读旧 JSON 字段名；真正上报走 jpkix_api。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src import settings
from src.pipeline import jpkix_api
from src.web_sites import resolve_push_web_id


logger = logging.getLogger(__name__)


def build_item_payload(
    item: Dict[str, Any],
    *,
    keyword: str = "",
    web_id: Any = None,
) -> Dict[str, Any]:
    """
    内部归一化 → 后端回传 item（6 必填）。
    额外保留 item_key 供本地 seen 吸收。
    """
    site = str(item.get("source_site") or "")
    item_id = str(item.get("id") or "")
    wid = resolve_push_web_id(web_id, site, item=item)
    hit = jpkix_api.build_hit_item(item, web_id=wid)
    if not hit:
        return {}
    out = dict(hit)
    # 若组包时仍带了订阅 webId，以规范后的 hit.webId 为准
    job_wid = web_id
    try:
        if job_wid is not None and int(job_wid) != int(out.get("webId") or 0):
            logger.info(
                "回传webId按商品规范 job=%s -> %s goods=%s site=%s",
                job_wid,
                out.get("webId"),
                out.get("goodsCode"),
                site,
            )
    except Exception:
        pass
    out["item_key"] = f"{site}:{item_id}" if site and item_id else hit.get("goodsCode")
    out["source_site"] = site
    out["id"] = item_id
    if keyword:
        out["keyword"] = keyword
    return out


def build_batch_payload(
    results_by_sub: Dict[str, Dict[str, Any]],
    *,
    run_id: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    interval_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    results_by_sub:
      {
        "12": {
          "backend_web_id": 28,
          "items": [ build_item_payload(...), ... ]
        }
      }
    """
    results: List[Dict[str, Any]] = []
    total = 0
    for sid, info in results_by_sub.items():
        raw_items = list(info.get("items") or [])
        items = []
        for it in raw_items:
            if not it:
                continue
            # 已是 6 字段则直接用；否则补造
            if "goodsCode" in it and "webId" in it:
                items.append(
                    {
                        "webId": int(it["webId"]),
                        "goodsCode": str(it["goodsCode"]),
                        "title": str(it.get("title") or "")[:512],
                        "price": int(it.get("price") or 0),
                        "itemUrl": str(it.get("itemUrl") or it.get("item_url") or ""),
                        "thumbnail": str(it.get("thumbnail") or ""),
                        # 本地辅助
                        "item_key": it.get("item_key") or "",
                        "id": it.get("id") or it.get("goodsCode"),
                        "source_site": it.get("source_site") or "",
                    }
                )
            else:
                built = build_item_payload(
                    it, web_id=info.get("backend_web_id") or it.get("webId")
                )
                if built:
                    items.append(built)
        if not items:
            continue
        # 回传体只用后端字段
        api_items = [
            {
                "webId": int(x["webId"]),
                "goodsCode": str(x["goodsCode"]),
                "title": str(x["title"]),
                "price": int(x["price"]),
                "itemUrl": str(x["itemUrl"]),
                "thumbnail": str(x["thumbnail"]),
            }
            for x in items
        ]
        total += len(api_items)
        try:
            sub_id: Any = int(sid)
        except Exception:
            sub_id = sid
        results.append(
            {
                "subscriptionId": sub_id,
                "items": api_items,
                # 本地 seen 用
                "_local_items": items,
            }
        )

    return {
        "results": results,
        "item_count": total,
        "run_id": run_id or "",
        "stats": stats or {},
    }


def push_batch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    优先走 subscription_api（jpkix）；未启用时兼容旧 backend_push.disabled 行为。
    """
    api_cfg = getattr(settings, "SUBSCRIPTION_API", None) or {}
    push_cfg = getattr(settings, "BACKEND_PUSH", None) or {}

    results_full = list(payload.get("results") or [])
    if not results_full:
        return {"ok": True, "skipped": True, "reason": "empty"}

    # 启用条件：subscription_api.enabled 或旧 backend_push.enabled
    enabled = bool(api_cfg.get("enabled")) or bool(push_cfg.get("enabled"))
    dry_run = bool(api_cfg.get("dry_run", push_cfg.get("dry_run", False)))

    if not enabled:
        logger.info(
            "backend 回传未启用，跳过 groups=%s items=%s",
            len(results_full),
            payload.get("item_count"),
        )
        return {"ok": True, "skipped": True, "reason": "disabled"}

    # 去掉本地辅助字段再提交
    api_results = [
        {"subscriptionId": r["subscriptionId"], "items": r["items"]}
        for r in results_full
    ]

    if dry_run:
        # 临时打开 jpkix dry_run
        old = api_cfg.get("dry_run")
        try:
            settings.SUBSCRIPTION_API["dry_run"] = True
            out = jpkix_api.push_hits(api_results)
        finally:
            settings.SUBSCRIPTION_API["dry_run"] = old
        # dry_run 也视为可吸收，避免本地堆积
        keys = []
        for r in results_full:
            for it in r.get("_local_items") or r.get("items") or []:
                k = it.get("item_key") or it.get("goodsCode")
                if k:
                    keys.append(str(k))
        out["accepted_item_keys"] = keys
        return out

    out = jpkix_api.push_hits(api_results)
    # 吸收键：goodsCode 与本地 item_key 都挂上
    keys: List[str] = []
    for r in results_full:
        for it in r.get("_local_items") or []:
            if it.get("item_key"):
                keys.append(str(it["item_key"]))
            if it.get("goodsCode"):
                keys.append(str(it["goodsCode"]))
    if not keys:
        keys = list(out.get("accepted_goods_codes") or [])
    out["accepted_item_keys"] = keys
    return out
