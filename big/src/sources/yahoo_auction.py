import json
import logging
import subprocess
from typing import Any, Dict, List, Optional

import requests

from src import settings


logger = logging.getLogger(__name__)

SEARCH_URL = "https://auctions.yahoo.co.jp/api/search/v3/search/items"
ITEM_WEB = "https://auctions.yahoo.co.jp/jp/auction/{auction_id}"

_FALLBACK = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
)


def _headers(cookie: str) -> Dict[str, str]:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.5",
        "Content-Type": "application/json",
        "Origin": "https://auctions.yahoo.co.jp",
        "Referer": "https://auctions.yahoo.co.jp/search/search",
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


def _curl_json(url: str, headers: Dict[str, str], body: str) -> Dict[str, Any]:
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
        "POST",
        "--url",
        url,
        "--data-binary",
        body,
    ]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed: {err}")
    out = (result.stdout or b"").decode("utf-8", errors="replace")
    return json.loads(out) if out else {}


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
        "raw": item,
    }


def search(subscription: Dict[str, Any]) -> List[Dict[str, Any]]:
    keyword = str(subscription.get("keyword") or "").strip()
    if not keyword:
        return []

    cfg = settings.YAHOO_AUCTION or {}
    cookie = str(cfg.get("cookie") or "").strip()
    # cookie 可选：无 cookie 亦可；SSL 失败时回退 curl
    # 新着：API 合法值为 -FIRST_START_TIME（无 NEWORDER）
    sort = str(cfg.get("sort") or "-FIRST_START_TIME").strip() or "-FIRST_START_TIME"
    # 排除闲置：sellerTypes 合法值 AUC_STORE / AUCTION / FLEAMARKET
    # 默认只要拍卖个人+店铺（类似煤炉 withAuction 的站点侧过滤）
    seller_types = cfg.get("seller_types")
    if seller_types is None:
        seller_types = ["AUCTION", "AUC_STORE"]
    if not isinstance(seller_types, list):
        seller_types = []

    body_obj = {
        "offset": 0,
        "limit": int(cfg.get("limit") or 24),
        "sort": sort,
        "isFinishToday": False,
        "isAuctionSubmit": False,
        "useSpeller": True,
        "src": "pc_result_open_clnht",
        "prefectureCodes": [],
        "exceptPrefectureCodes": [],
        "conditions": [],
        "sellerTypes": seller_types,
        "exceptCategoryIds": [],
        "brandIds": [],
        "aucUserIds": [],
        "submitType": "ALL",
        "shpSpec": [],
        "query": keyword,
        "categoryIds": [0],
        "imageSize": "SMALL",
        "imageShape": "RAW",
        "destinationPrefectureCode": "13",
        "status": str(cfg.get("status") or "OPEN"),
        "rewriteQueryTypes": ["WAND_SEARCH"],
    }
    body_text = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
    price_min = int(subscription.get("price_min") or 0)
    price_max = int(subscription.get("price_max") or 0)
    headers = _headers(cookie)

    session = requests.Session()
    session.trust_env = False
    data: Dict[str, Any] = {}
    try:
        resp = session.post(
            SEARCH_URL,
            data=body_text.encode("utf-8"),
            headers=headers,
            timeout=(15, 45),
        )
        if resp.status_code >= 400:
            logger.warning(
                "Yahoo Auction requests status=%s，尝试 curl。body=%s",
                resp.status_code,
                (resp.text or "")[:300],
            )
            data = _curl_json(SEARCH_URL, headers, body_text)
        else:
            data = resp.json() if resp.content else {}
            logger.info("Yahoo Auction 请求方式: requests cookie=%s", "yes" if cookie else "no")
    except _FALLBACK as exc:
        logger.warning("Yahoo Auction requests 失败，回退 curl: %s", exc)
        try:
            data = _curl_json(SEARCH_URL, headers, body_text)
            logger.info("Yahoo Auction 请求方式: curl cookie=%s", "yes" if cookie else "no")
        except Exception as curl_exc:
            logger.error("Yahoo Auction curl 也失败: %s", curl_exc)
            raise
    except Exception as exc:
        logger.error("Yahoo Auction 搜索异常: %s", exc)
        raise

    items = data.get("items") or []
    out: List[Dict[str, Any]] = []
    dropped_flea = 0
    for it in items:
        if not isinstance(it, dict) or not it.get("auctionId"):
            continue
        if _is_fleamarket_item(it):
            dropped_flea += 1
            continue
        norm = _normalize(it)
        price = norm["price"]
        if price_min > 0 and price < price_min:
            continue
        if price_max > 0 and price > price_max:
            continue
        out.append(norm)

    logger.info(
        "Yahoo Auction 搜索完成: keyword=%s sort=%s sellerTypes=%s 返回=%s 本地再剔闲置=%s",
        keyword,
        sort,
        seller_types,
        len(out),
        dropped_flea,
    )
    return out
