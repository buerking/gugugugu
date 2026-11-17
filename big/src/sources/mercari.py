import json
import logging
import subprocess
import uuid
from typing import Any, Dict, List, Optional

import requests

from src import settings
from src.mercari_dpop import make_dpop_jwt


logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.mercari.jp/v2/entities:search"
ITEM_GET_URL = "https://api.mercari.jp/items/get"
ITEM_WEB = "https://jp.mercari.com/item/{item_id}"

_FALLBACK = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
)


def _curl_json(method: str, url: str, headers: Dict[str, str], body: Optional[str] = None) -> Dict[str, Any]:
    cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--compressed",
        "--connect-timeout",
        "15",
        "--max-time",
        "45",
        "-X",
        method.upper(),
        "--url",
        url,
    ]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    if body is not None:
        cmd.extend(["-H", "Content-Type: application/json", "--data-binary", body])
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed: {err}")
    out = (result.stdout or b"").decode("utf-8", errors="replace")
    return json.loads(out) if out else {}


def _detect_auction(item: Dict[str, Any]) -> bool:
    """根据商品字段判断是否为拍卖出价商品。auction=null 视为一口价。"""
    for obj in (item, item.get("item") if isinstance(item.get("item"), dict) else None):
        if not isinstance(obj, dict):
            continue
        if "auction" in obj:
            return obj.get("auction") not in (None, "", {}, [])
        if obj.get("auctionInfo") or obj.get("auction_info"):
            return True
        item_type = str(obj.get("itemType") or obj.get("item_type") or "").upper()
        if "AUCTION" in item_type and "MERCARI" not in item_type and "BEYOND" not in item_type:
            return True
    return False


def _unwrap_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(entity, dict):
        return {}
    for key in ("item", "product", "data"):
        nested = entity.get(key)
        if isinstance(nested, dict) and (nested.get("id") or nested.get("itemId")):
            return nested
    return entity


def _normalize(entity: Dict[str, Any]) -> Dict[str, Any]:
    item = _unwrap_entity(entity)
    item_id = str(item.get("id") or item.get("itemId") or entity.get("id") or "")
    thumbs = item.get("thumbnails") or item.get("thumbnailUrls") or []
    if isinstance(thumbs, list) and thumbs:
        thumb = thumbs[0] if isinstance(thumbs[0], str) else (thumbs[0] or {}).get("url", "")
    else:
        thumb = str(item.get("thumbnail") or item.get("thumbnails") or "")
    price = item.get("price")
    if price is None:
        price = item.get("priceJPY") or 0
    is_auction = _detect_auction(item) or _detect_auction(entity)
    status_raw = item.get("status") or entity.get("status") or "on_sale"
    return {
        "id": item_id,
        "name": item.get("name") or item.get("title") or "",
        "price": int(price or 0),
        "thumbnail": thumb,
        "status": str(status_raw).lower(),
        "item_url": ITEM_WEB.format(item_id=item_id),
        "source_site": "mercari",
        "listing_type": "auction" if is_auction else "buy_now",
        "created": item.get("created") or item.get("createdAt"),
        "updated": item.get("updated") or item.get("updatedAt"),
        "raw": entity,
    }


def enrich_auction_flag(item: Dict[str, Any]) -> Dict[str, Any]:
    """必要时拉详情确认拍卖字段。"""
    if item.get("listing_type") == "auction":
        return item
    item_id = item.get("id") or ""
    if not item_id:
        return item
    try:
        htu = f"{ITEM_GET_URL}?id={item_id}"
        dpop = make_dpop_jwt(htm="GET", htu=htu)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
            "X-Platform": "web",
            "Origin": "https://jp.mercari.com",
            "Referer": "https://jp.mercari.com/",
            "DPoP": dpop,
        }
        session = requests.Session()
        session.trust_env = False
        resp = session.get(ITEM_GET_URL, params={"id": item_id}, headers=headers, timeout=(15, 45))
        if resp.status_code >= 400:
            payload = _curl_json("GET", htu, headers)
        else:
            payload = resp.json() if resp.content else {}
        data = payload.get("data") or payload
        if _detect_auction(data if isinstance(data, dict) else {}):
            item["listing_type"] = "auction"
            item["raw_detail"] = data
    except Exception as exc:
        logger.debug("Mercari 详情确认拍卖失败 %s: %s", item_id, exc)
    return item


def search(subscription: Dict[str, Any]) -> List[Dict[str, Any]]:
    keyword = str(subscription.get("keyword") or "").strip()
    if not keyword:
        return []

    cfg = settings.MERCARI
    page_size = int(cfg.get("page_size") or 120)
    # 订阅可单独关闭煤炉拍卖；未写则沿用全局 with_auction
    if "with_auction" in subscription:
        with_auction = bool(subscription.get("with_auction"))
    else:
        with_auction = bool(cfg.get("with_auction", True))
    price_min = int(subscription.get("price_min") or 0)
    price_max = int(subscription.get("price_max") or 0)
    exclude_keyword = str(subscription.get("exclude_keyword") or "")

    body = {
        "userId": "",
        "config": {"responseToggles": ["QUERY_SUGGESTION_WEB_1"]},
        "pageSize": page_size,
        "pageToken": "",
        "searchSessionId": uuid.uuid4().hex,
        "source": "BaseSerp",
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword,
            "excludeKeyword": exclude_keyword,
            "sort": str(cfg.get("sort") or "SORT_CREATED_TIME"),
            "order": str(cfg.get("order") or "ORDER_DESC"),
            "status": ["STATUS_ON_SALE"],
            "sizeId": [],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
            "priceMin": price_min,
            "priceMax": price_max,
            "itemConditionId": [],
            "shippingPayerId": [],
            "shippingFromArea": [],
            "shippingMethod": [],
            "colorId": [],
            "hasCoupon": False,
            "attributes": [],
            "itemTypes": [],
            "skuIds": [],
            "shopIds": [],
            "excludeShippingMethodIds": [],
        },
        "serviceFrom": "suruga",
        "withItemBrand": True,
        "withItemSize": False,
        "withItemPromotions": True,
        "withItemSizes": True,
        "withShopname": False,
        "useDynamicAttribute": True,
        "withSuggestedItems": True,
        "withOfferPricePromotion": True,
        "withProductSuggest": True,
        "withParentProducts": False,
        "withProductArticles": True,
        "withSearchConditionId": False,
        "withAuction": with_auction,
        "laplaceDeviceUuid": str(cfg.get("laplace_device_uuid") or uuid.uuid4().hex),
    }
    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    dpop = make_dpop_jwt(htm="POST", htu=SEARCH_URL)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "X-Platform": "web",
        "Origin": "https://jp.mercari.com",
        "Referer": "https://jp.mercari.com/",
        "DPoP": dpop,
        "Content-Type": "application/json",
    }

    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.post(SEARCH_URL, data=body_text.encode("utf-8"), headers=headers, timeout=(15, 45))
        if resp.status_code >= 400:
            logger.error("Mercari 搜索失败 status=%s body=%s", resp.status_code, (resp.text or "")[:500])
            payload = _curl_json("POST", SEARCH_URL, headers, body_text)
        else:
            payload = resp.json() if resp.content else {}
            logger.info("Mercari 请求方式: requests")
    except _FALLBACK as err:
        logger.warning("Mercari requests 失败，回退 curl: %s", err)
        payload = _curl_json("POST", SEARCH_URL, headers, body_text)
        logger.info("Mercari 请求方式: curl")

    items_raw = (
        payload.get("items")
        or payload.get("data")
        or (payload.get("result") or {}).get("items")
        or []
    )
    if isinstance(items_raw, dict):
        items_raw = items_raw.get("items") or []

    out: List[Dict[str, Any]] = []
    for entity in items_raw:
        if not isinstance(entity, dict):
            continue
        # 仅在售（接口可能仍返回 sold_out / trading）
        st = str(entity.get("status") or "").upper()
        if st and "ON_SALE" not in st and st not in ("ON_SALE", ""):
            if "SOLD" in st or "TRADING" in st:
                continue
        norm = _normalize(entity)
        if not norm.get("id"):
            continue
        # 响应已带 auction 字段时无需再拉详情；仅缺字段时少量补查
        if "auction" not in entity and len(out) < 15:
            norm = enrich_auction_flag(norm)
        out.append(norm)

    if not with_auction:
        before = len(out)
        out = [it for it in out if it.get("listing_type") != "auction"]
        dropped = before - len(out)
        if dropped:
            logger.info("Mercari 排除拍卖: keyword=%s 丢掉=%s", keyword, dropped)

    logger.info("Mercari 搜索完成: keyword=%s 返回=%s with_auction=%s", keyword, len(out), with_auction)
    return out
