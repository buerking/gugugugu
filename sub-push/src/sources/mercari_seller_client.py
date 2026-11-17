"""
煤炉卖家上新：与关键词同一套站点能力（一口价+拍卖合在一起）。
HTTP 统一走 curl（Windows Schannel），避免 requests/OpenSSL SSLEOF。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from src.mercari_dpop import make_dpop_jwt
from src.net.curl_http import curl_json
from src.sources.mercari_web import _detect_auction


logger = logging.getLogger(__name__)


MERCARI_SELLER_ITEMS_URL = "https://api.mercari.jp/items/get_items"
MERCARI_ITEM_WEB_URL_TEMPLATE = "https://jp.mercari.com/item/{item_id}"


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    item_id = item.get("id") or ""
    thumbnails = item.get("thumbnails") or []
    thumb = thumbnails[0] if thumbnails else ""
    seller = item.get("seller") or {}
    condition = item.get("item_condition") or {}
    status = (item.get("status") or "").lower()
    is_auction = _detect_auction(item)
    return {
        "id": item_id,
        "name": item.get("name") or "",
        "price": int(item.get("price") or 0),
        "thumbnail": thumb,
        "status": status or "unknown",
        "item_condition": {
            "id": condition.get("id", "") if isinstance(condition, dict) else "",
            "name": condition.get("name", "") if isinstance(condition, dict) else "",
        },
        "shipping_payer": item.get("shipping_payer") or {"code": "unknown", "name": "未知"},
        "seller": {
            "id": str(seller.get("id") or ""),
            "name": str(seller.get("name") or seller.get("id") or ""),
        },
        "item_url": MERCARI_ITEM_WEB_URL_TEMPLATE.format(item_id=item_id),
        "source_site": "mercari",
        "created": item.get("created"),
        "listing_type": "auction" if is_auction else "buy_now",
    }


def search_seller_new_listings(
    *,
    seller_id: str,
    created_after_date: str,
    limit: int = 100,
    as_shop: bool = False,
) -> List[Dict[str, Any]]:
    sid = str(seller_id or "").strip()
    if not sid:
        return []

    created_after_ts = _to_int_or_none(created_after_date)
    use_shop = bool(as_shop) or (not sid.isdigit())
    params: Dict[str, Any] = {
        "limit": max(1, min(int(limit), 200)),
        "status": "on_sale",
    }
    if use_shop:
        params["shop_id"] = sid
    else:
        params["seller_id"] = sid

    # DPoP 绑定完整 URL（含 query）
    htu = f"{MERCARI_SELLER_ITEMS_URL}?{urlencode(params)}"
    dpop = make_dpop_jwt(htm="GET", htu=htu)
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "X-Platform": "web",
        "DPoP": dpop,
    }

    payload = curl_json("GET", MERCARI_SELLER_ITEMS_URL, headers=headers, params=params)
    items = payload.get("items") or payload.get("data") or []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (item.get("status") or "").lower() != "on_sale":
            continue
        created_ts = _to_int_or_none(item.get("created"))
        if (
            created_after_ts is not None
            and created_ts is not None
            and created_ts < created_after_ts
        ):
            continue
        out.append(_normalize_item(item))

    logger.debug(
        "MercariSeller: id=%s mode=%s 返回=%s 命中=%s created_after=%s",
        sid,
        "shop" if use_shop else "seller",
        len(items),
        len(out),
        created_after_date,
    )
    return out
