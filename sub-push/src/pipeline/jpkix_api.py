"""
jpkix 上新订阅 API：表单 data + sign。

接口：
- get-search-subscriptions（翻页下发）
- push-search-subscription-hits（回传命中）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from src import settings


logger = logging.getLogger(__name__)

_GOODS_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def dumps_data(obj: Any) -> str:
    """签名用：紧凑 JSON，签完后不得再改。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def make_sign(data_str: str, key: str = "purchaseApi") -> str:
    raw = f"data={data_str}&key={key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "SUBSCRIPTION_API", None) or {})


def api_url(action: str) -> str:
    cfg = _cfg()
    base = str(cfg.get("base_url") or "").rstrip("/")
    # base 形如 https://p.jpkix.com/yii/web/index.php
    if not base:
        raise RuntimeError("subscription_api.base_url 未配置")
    route = str(cfg.get("route_prefix") or "home/api")
    return f"{base}?r={route}/{action}"


def post_signed(action: str, payload: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
    cfg = _cfg()
    data_str = dumps_data(payload)
    key = str(cfg.get("sign_key") or "purchaseApi")
    sign = make_sign(data_str, key=key)
    url = api_url(action)
    to = float(timeout if timeout is not None else cfg.get("timeout_seconds") or 30)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "sub-push/1.0",
    }
    try:
        from src.failover.runtime_pulse import note_request

        note_request("jpkix", action)
    except Exception:
        pass
    resp = requests.post(
        url,
        data={"data": data_str, "sign": sign},
        headers=headers,
        timeout=to,
    )
    text = (resp.text or "")[:800]
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"success": False, "message": "非 JSON 响应", "data": None, "code": resp.status_code}
    logger.info(
        "jpkix %s http=%s success=%s code=%s msg=%s",
        action,
        resp.status_code,
        body.get("success"),
        body.get("code"),
        str(body.get("message") or "")[:120],
    )
    if resp.status_code >= 400 and not body:
        resp.raise_for_status()
    return body


def fetch_all_subscriptions() -> List[Dict[str, Any]]:
    """从 lastId=0 翻页拉完全部生效订阅。"""
    cfg = _cfg()
    action = str(cfg.get("get_action") or "get-search-subscriptions")
    last_id = 0
    all_rows: List[Dict[str, Any]] = []
    pages = 0
    max_pages = int(cfg.get("max_pages") or 100)
    while pages < max_pages:
        pages += 1
        body = post_signed(action, {"lastId": int(last_id)})
        if not body.get("success"):
            raise RuntimeError(
                f"下发订阅失败: code={body.get('code')} message={body.get('message')}"
            )
        data = body.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError("下发订阅 data 格式错误")
        chunk = data.get("list") or []
        if not isinstance(chunk, list):
            raise RuntimeError("下发订阅 list 不是数组")
        all_rows.extend([x for x in chunk if isinstance(x, dict)])
        has_more = bool(data.get("hasMore"))
        next_id = data.get("nextLastId")
        logger.info(
            "下发订阅页#%s lastId=%s got=%s hasMore=%s nextLastId=%s",
            pages,
            last_id,
            len(chunk),
            has_more,
            next_id,
        )
        if not has_more:
            break
        try:
            last_id = int(next_id)
        except Exception as exc:
            raise RuntimeError(f"nextLastId 非法: {next_id}") from exc
    return all_rows


def sanitize_goods_code(raw: Any) -> str:
    s = str(raw or "").strip()
    if _GOODS_CODE_RE.match(s):
        return s
    # 尽量清洗：去掉非法字符
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", s)[:64]
    return cleaned


def build_hit_item(
    item: Dict[str, Any],
    *,
    web_id: int,
) -> Optional[Dict[str, Any]]:
    """后端 6 必填字段。"""
    goods = sanitize_goods_code(item.get("id") or item.get("goodsCode") or item.get("item_id"))
    title = str(item.get("name") or item.get("title") or "").strip()[:512]
    try:
        price = int(item.get("price") or 0)
    except Exception:
        price = 0
    if price < 0:
        price = 0
    # 最多 10 位
    if price > 9_999_999_999:
        price = 9_999_999_999
    item_url = str(item.get("item_url") or item.get("itemUrl") or "").strip()[:512]
    thumb = str(item.get("thumbnail") or "").strip()[:1024]
    if not goods or not title or not item_url or not thumb:
        return None
    if not (item_url.startswith("http://") or item_url.startswith("https://")):
        return None
    if not (thumb.startswith("http://") or thumb.startswith("https://")):
        return None
    return {
        "webId": int(web_id),
        "goodsCode": goods,
        "title": title,
        "price": price,
        "itemUrl": item_url,
        "thumbnail": thumb,
    }


def chunk_results(
    results: List[Dict[str, Any]],
    *,
    max_groups: int = 1000,
    max_items: int = 5000,
) -> List[List[Dict[str, Any]]]:
    """按后端上限分批。"""
    batches: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_items = 0
    for group in results:
        items = list(group.get("items") or [])
        n = len(items)
        if n == 0:
            continue
        # 单组过大：拆 items
        offset = 0
        while offset < n:
            room_groups = max_groups - len(cur)
            room_items = max_items - cur_items
            if room_groups <= 0 or room_items <= 0:
                batches.append(cur)
                cur = []
                cur_items = 0
                room_items = max_items
            take = min(n - offset, room_items, max_items)
            piece = {
                "subscriptionId": group["subscriptionId"],
                "items": items[offset : offset + take],
            }
            cur.append(piece)
            cur_items += take
            offset += take
            if len(cur) >= max_groups or cur_items >= max_items:
                batches.append(cur)
                cur = []
                cur_items = 0
    if cur:
        batches.append(cur)
    return batches


def push_hits(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    results: [{subscriptionId, items:[{webId,goodsCode,...}]}]
    """
    cfg = _cfg()
    action = str(cfg.get("push_action") or "push-search-subscription-hits")
    max_groups = int(cfg.get("max_groups_per_request") or 1000)
    max_items = int(cfg.get("max_items_per_request") or 5000)
    batches = chunk_results(results, max_groups=max_groups, max_items=max_items)
    if not batches:
        return {"ok": True, "skipped": True, "reason": "empty"}

    if cfg.get("dry_run"):
        logger.info(
            "[dry_run] push-hits batches=%s groups=%s items=%s",
            len(batches),
            len(results),
            sum(len(g.get("items") or []) for g in results),
        )
        return {"ok": True, "dry_run": True, "batches": len(batches), "payload_preview": results[:2]}

    all_accepted_codes: List[str] = []
    details: List[Any] = []
    for i, batch in enumerate(batches):
        body = post_signed(action, {"results": batch})
        if not body.get("success"):
            raise RuntimeError(
                f"回传失败 batch={i}: code={body.get('code')} message={body.get('message')}"
            )
        data = body.get("data") or {}
        rows = (data.get("results") if isinstance(data, dict) else None) or []
        details.append(rows)
        # 无明细时：本批全部视为可吸收
        for g in batch:
            for it in g.get("items") or []:
                code = it.get("goodsCode")
                if code:
                    all_accepted_codes.append(str(code))
    return {
        "ok": True,
        "batches": len(batches),
        "accepted_goods_codes": all_accepted_codes,
        "details": details,
    }
