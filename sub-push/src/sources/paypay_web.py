import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlencode

from src import settings
from src.net.curl_http import curl_json


logger = logging.getLogger(__name__)

BASE = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"
ITEM_WEB = "https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

# PayPay 串行：全局同一时刻只打一个。
# 间隔仅在「跨队列/跨线程」交接时生效；同 worker 连续打不睡（请求本身已占时）。
_PAYPAY_SEM = threading.Semaphore(1)
_paypay_last_at = 0.0
_paypay_last_tid: Optional[int] = None
_paypay_lock = threading.Lock()


def _gap_ms() -> int:
    """跨队列间隔：优先 [min,max] 均匀随机（μs 级），否则退回固定 request_gap_ms。"""
    cfg = settings.PAYPAY or {}
    try:
        lo = int(cfg.get("request_gap_ms_min") or 0)
        hi = int(cfg.get("request_gap_ms_max") or 0)
    except Exception:
        lo = hi = 0
    if lo > 0 and hi >= lo:
        return random.randint(lo, hi)
    try:
        return max(0, int(cfg.get("request_gap_ms") or 200))
    except Exception:
        return 200


@contextmanager
def paypay_slot() -> Iterator[None]:
    """关键词/卖家共用：串行；同线程连续请求跳过 gap，跨线程才等随机/固定间隔。"""
    global _paypay_last_at, _paypay_last_tid
    tid = threading.get_ident()
    _PAYPAY_SEM.acquire()
    wait = 0.0
    gap_ms = 0
    with _paypay_lock:
        same_queue = _paypay_last_tid is not None and _paypay_last_tid == tid
        if not same_queue and _paypay_last_at > 0:
            gap_ms = _gap_ms()
            wait = (gap_ms / 1000.0) - (time.time() - _paypay_last_at)
    if wait > 0:
        logger.info("PayPay跨队列间隔等待 %.0fms (目标=%sms)", wait * 1000, gap_ms)
        time.sleep(wait)
    try:
        yield
    finally:
        with _paypay_lock:
            _paypay_last_at = time.time()
            _paypay_last_tid = tid
        _PAYPAY_SEM.release()



def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        "Referer": "https://paypayfleamarket.yahoo.co.jp/",
        "Origin": "https://paypayfleamarket.yahoo.co.jp",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    item_id = str(item.get("id") or "")
    return {
        "id": item_id,
        "name": item.get("title") or item.get("name") or "",
        "price": int(item.get("price") or 0),
        "thumbnail": item.get("thumbnailImageUrl") or item.get("imageUrl") or "",
        "status": "on_sale",
        "item_url": ITEM_WEB.format(item_id=item_id),
        "source_site": "paypay",
        "listing_type": "buy_now",
        "created": item.get("openTime") or item.get("created"),
        "updated": item.get("updateTime") or item.get("updated") or item.get("openTime"),
    }


def search(subscription: Dict[str, Any]) -> List[Dict[str, Any]]:
    keyword = str(subscription.get("keyword") or "").strip()
    if not keyword:
        return []

    results = max(1, min(40, int((settings.PAYPAY or {}).get("results") or 30)))
    params: Dict[str, Any] = {
        "results": results,
        "imageShape": "square",
        "sort": "openTime",
        "order": "DESC",
        "webp": "false",
        "itemStatus": "open",
        "offset": 0,
        "query": keyword,
        "queryTarget": ":8",
        "module": "catalog:hit:21",
        "rewriteQueryType": "WAND",
        "optional": "searchCondition",
    }
    price_min = int(subscription.get("price_min") or 0)
    price_max = int(subscription.get("price_max") or 0)
    if price_min > 0:
        params["minPrice"] = price_min
    if price_max > 0:
        params["maxPrice"] = price_max

    url = f"{BASE}?{urlencode(params)}"
    t0 = time.time()
    with paypay_slot():
        data = curl_json("GET", url, headers=_headers())
    items = data.get("items") or []
    out = [_normalize(it) for it in items if isinstance(it, dict) and it.get("id")]
    logger.info(
        "PayPay完成 kw=%s api=%s 耗时=%.2fs",
        keyword[:40],
        len(out),
        time.time() - t0,
    )
    return out
