import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src import settings
from src.net.curl_http import curl_json


logger = logging.getLogger(__name__)

SEARCH_URL = "https://auctions.yahoo.co.jp/api/search/v3/search/items"
ITEM_WEB = "https://auctions.yahoo.co.jp/jp/auction/{auction_id}"


def _headers(cookie: str, *, referer: str = "") -> Dict[str, str]:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.5",
        "Content-Type": "application/json",
        "Origin": "https://auctions.yahoo.co.jp",
        "Referer": referer or "https://auctions.yahoo.co.jp/search/search",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def _is_fleamarket_item(item: Dict[str, Any]) -> bool:
    """雅虎拍卖结果会混入 PayPay 闲置（isFleamarketItem / seller.type=FLEAMARKET）。"""
    if item.get("isFleamarketItem") is True:
        return True
    seller = item.get("seller") or {}
    if isinstance(seller, dict) and str(seller.get("type") or "").upper() == "FLEAMARKET":
        return True
    return False


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    auction_id = str(item.get("auctionId") or "")
    is_fixed = bool(item.get("isFixedPrice"))
    return {
        "id": auction_id,
        "name": item.get("title") or "",
        "price": int(item.get("price") or item.get("buyNowPrice") or 0),
        "thumbnail": item.get("imageUrl") or "",
        "status": "open",
        "item_url": ITEM_WEB.format(auction_id=auction_id),
        "source_site": "yahoo_auction",
        "listing_type": "buy_now" if is_fixed else "auction",
        "created": item.get("startTime"),
        "updated": item.get("startTime"),
        "end_time": item.get("endTime"),
        "bid_count": item.get("bidCount"),
    }


def _parse_start_ts(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    try:
        if s.isdigit():
            return int(s)
        # 2026-11-03T09:34:37+09:00
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def _seller_types(cfg: Dict[str, Any]) -> List[str]:
    seller_types = cfg.get("seller_types")
    if seller_types is None:
        seller_types = ["AUCTION", "AUC_STORE"]
    if not isinstance(seller_types, list):
        return []
    return list(seller_types)


def _build_body(
    *,
    query: str = "",
    auc_user_ids: Optional[List[str]] = None,
    sort: str = "-FIRST_START_TIME",
    limit: int = 24,
    status: str = "OPEN",
    seller_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "offset": 0,
        "limit": int(limit or 24),
        "sort": sort,
        "isFinishToday": False,
        "isAuctionSubmit": False,
        "useSpeller": True,
        "src": "pc_result_open_clnht",
        "prefectureCodes": [],
        "exceptPrefectureCodes": [],
        "conditions": [],
        "sellerTypes": list(seller_types or []),
        "exceptCategoryIds": [],
        "brandIds": [],
        "aucUserIds": list(auc_user_ids or []),
        "submitType": "ALL",
        "shpSpec": [],
        "query": query or "",
        "categoryIds": [0],
        "imageSize": "SMALL",
        "imageShape": "RAW",
        "destinationPrefectureCode": "13",
        "status": status,
        "rewriteQueryTypes": ["WAND_SEARCH"],
    }


def _post_search(
    body_obj: Dict[str, Any],
    *,
    cookie: str = "",
    referer: str = "",
) -> Dict[str, Any]:
    body_text = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
    headers = _headers(cookie, referer=referer)
    # 该接口成功时常返回 201；curl_json 仅把 >=400 当失败
    return curl_json("POST", SEARCH_URL, headers=headers, body=body_text)


def _collect_items(
    data: Dict[str, Any],
    *,
    price_min: int = 0,
    price_max: int = 0,
    created_after_ts: Optional[int] = None,
    drop_fleamarket: bool = True,
    expect_seller_id: str = "",
) -> Tuple[List[Dict[str, Any]], int, int]:
    items = data.get("items") or []
    out: List[Dict[str, Any]] = []
    dropped_flea = 0
    dropped_time = 0
    expect = str(expect_seller_id or "").strip()
    for it in items:
        if not isinstance(it, dict) or not it.get("auctionId"):
            continue
        if expect:
            uid = str(((it.get("seller") or {}).get("userId") or "")).strip()
            if uid != expect:
                continue
        if drop_fleamarket and _is_fleamarket_item(it):
            dropped_flea += 1
            continue
        if created_after_ts is not None:
            st = _parse_start_ts(it.get("startTime"))
            if st is not None and st < created_after_ts:
                dropped_time += 1
                continue
        norm = _normalize(it)
        price = norm["price"]
        if price_min > 0 and price < price_min:
            continue
        if price_max > 0 and price > price_max:
            continue
        out.append(norm)
    return out, dropped_flea, dropped_time


def search(subscription: Dict[str, Any]) -> List[Dict[str, Any]]:
    keyword = str(subscription.get("keyword") or "").strip()
    if not keyword:
        return []

    cfg = settings.YAHOO_AUCTION or {}
    cookie = str(cfg.get("cookie") or "").strip()
    sort = str(cfg.get("sort") or "-FIRST_START_TIME").strip() or "-FIRST_START_TIME"
    seller_types = _seller_types(cfg)
    body_obj = _build_body(
        query=keyword,
        auc_user_ids=[],
        sort=sort,
        limit=int(cfg.get("limit") or 24),
        status=str(cfg.get("status") or "OPEN"),
        seller_types=seller_types,
    )
    try:
        data = _post_search(body_obj, cookie=cookie)
    except Exception as exc:
        logger.error("Yahoo Auction 搜索异常: %s", exc)
        raise

    price_min = int(subscription.get("price_min") or 0)
    price_max = int(subscription.get("price_max") or 0)
    out, dropped_flea, _ = _collect_items(
        data, price_min=price_min, price_max=price_max, drop_fleamarket=True
    )
    logger.info(
        "Yahoo Auction 搜索完成: keyword=%s sort=%s sellerTypes=%s 返回=%s 本地再剔闲置=%s",
        keyword,
        sort,
        seller_types,
        len(out),
        dropped_flea,
    )
    return out


def search_by_seller_id(
    seller_id: str,
    created_after_date: str = "",
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    雅虎拍卖卖家上新。
    卖家页 https://auctions.yahoo.co.jp/seller/{aucUserId}
    正式接口：POST search/v3，body.aucUserIds = [aucUserId]
    （页面 __NEXT_DATA__ 里也是同一 userId；无需 F12 XHR 也能确认。）
    """
    sid = str(seller_id or "").strip()
    if not sid:
        return []

    cfg = settings.YAHOO_AUCTION or {}
    cookie = str(cfg.get("cookie") or "").strip()
    sort = str(cfg.get("sort") or "-FIRST_START_TIME").strip() or "-FIRST_START_TIME"
    seller_types = _seller_types(cfg)
    body_obj = _build_body(
        query="",
        auc_user_ids=[sid],
        sort=sort,
        limit=int(limit or cfg.get("limit") or 48),
        status=str(cfg.get("status") or "OPEN"),
        seller_types=seller_types,
    )
    referer = f"https://auctions.yahoo.co.jp/seller/{sid}"
    try:
        data = _post_search(body_obj, cookie=cookie, referer=referer)
    except Exception as exc:
        logger.error("Yahoo Auction 卖家搜索失败 seller=%s: %s", sid, exc)
        raise

    created_after_ts: Optional[int] = None
    if created_after_date not in (None, ""):
        try:
            created_after_ts = int(float(str(created_after_date)))
        except Exception:
            created_after_ts = _parse_start_ts(created_after_date)

    out, dropped_flea, dropped_time = _collect_items(
        data,
        created_after_ts=created_after_ts,
        drop_fleamarket=True,
        expect_seller_id=sid,
    )
    raw_n = len(data.get("items") or [])
    if raw_n and not out and created_after_ts is None:
        # 有返回但无一属于该卖家 → aucUserIds 可能未生效
        logger.error(
            "Yahoo Auction 卖家搜索疑似未生效 seller=%s raw=%s（aucUserIds 可能被忽略）",
            sid,
            raw_n,
        )
        return []

    logger.info(
        "Yahoo Auction 卖家搜索完成: seller=%s sort=%s 命中=%s 剔闲置=%s 剔时间=%s raw=%s",
        sid,
        sort,
        len(out),
        dropped_flea,
        dropped_time,
        raw_n,
    )
    return out
