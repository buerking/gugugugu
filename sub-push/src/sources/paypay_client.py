import datetime
import logging
from typing import Any, Dict, List, Optional

from src import settings
from src.mapping import map_subscription_to_source_request
from src.net.curl_http import curl_json
from src.sources.paypay_web import paypay_slot


logger = logging.getLogger(__name__)


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _parse_iso_to_timestamp(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.datetime.fromisoformat(value).timestamp())
    except Exception:
        return None


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
        "Origin": "https://paypayfleamarket.yahoo.co.jp",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    item_id = item.get("id", "")
    item_condition = item.get("condition") or "unknown"
    item_status = (item.get("itemStatus") or "").upper()
    return {
        "id": item_id,
        "name": item.get("title") or "",
        "price": int(item.get("price", 0) or 0),
        "thumbnail": item.get("thumbnailImageUrl") or "",
        "status": "on_sale" if item_status == "OPEN" else "sold_out",
        "item_condition": {"id": item_condition, "name": item_condition},
        "shipping_payer": {"code": "unknown", "name": "未知"},
        "seller": {
            "id": (item.get("seller") or {}).get("id", ""),
            "name": (item.get("seller") or {}).get("id", ""),
        },
        "item_url": settings.PAYPAY_ITEM_URL_TEMPLATE.format(item_id=item_id),
        "source_site": "paypay",
        "listing_type": "buy_now",
        "created": item.get("openTime") or item.get("created") or "",
        "updated": item.get("updateTime") or item.get("openTime") or "",
        "openTime": item.get("openTime") or "",
    }


def search(subscription: Dict[str, Any], created_after_date: str) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = dict(settings.PAYPAY_DEFAULT_PARAMS)
    mapped = map_subscription_to_source_request("paypay", subscription, created_after_date)
    params["query"] = mapped.get("query", "")

    min_price = _to_int_or_none(mapped.get("minPrice"))
    max_price = _to_int_or_none(mapped.get("maxPrice"))
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price

    created_after_ts = _to_int_or_none(created_after_date)
    try:
        with paypay_slot():
            payload = curl_json(
                "GET",
                settings.PAYPAY_SEARCH_URL,
                headers=_headers(),
                params=params,
            )
        items = payload.get("items", [])
        out: List[Dict[str, Any]] = []
        for item in items:
            open_ts = _parse_iso_to_timestamp(item.get("openTime"))
            if created_after_ts is not None and open_ts is not None and open_ts < created_after_ts:
                continue
            out.append(_normalize(item))
        logger.debug(
            "PayPay 搜索完成: 时间截尾=%s 返回=%s 命中=%s",
            created_after_date,
            len(items),
            len(out),
        )
        return out
    except Exception as exc:
        logger.error("PayPay 搜索失败: %s", exc)
        return []


def search_by_seller_id(seller_id: str, created_after_date: str) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = dict(settings.PAYPAY_DEFAULT_PARAMS)
    params["sellerIds"] = seller_id
    params["isExtendRange"] = "true"
    params["itemStatus"] = "open"
    params["includeBulkPurchaseItem"] = "true"
    params["sort"] = "openTime"
    params["order"] = "DESC"
    params["query"] = ""
    params.pop("queryTarget", None)

    created_after_ts = _to_int_or_none(created_after_date)
    try:
        with paypay_slot():
            payload = curl_json(
                "GET",
                settings.PAYPAY_SEARCH_URL,
                headers=_headers(),
                params=params,
            )
        items = payload.get("items", [])
        out: List[Dict[str, Any]] = []
        for item in items:
            open_ts = _parse_iso_to_timestamp(item.get("openTime"))
            if created_after_ts is not None and open_ts is not None and open_ts < created_after_ts:
                continue
            out.append(_normalize(item))
        logger.debug(
            "PayPaySeller 完成: seller=%s 返回=%s 命中=%s",
            seller_id,
            len(items),
            len(out),
        )
        return out
    except Exception as exc:
        logger.error("PayPaySeller 失败 seller=%s: %s", seller_id, exc)
        return []
