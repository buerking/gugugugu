import json
import logging
import subprocess
from typing import Any, Dict, List
from urllib.parse import urlencode

import requests

from src import settings


logger = logging.getLogger(__name__)

BASE = "https://paypayfleamarket.yahoo.co.jp/api/v1/search"
ITEM_WEB = "https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

_FALLBACK = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
)


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


def _curl_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
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
        "--url",
        url,
    ]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed: {err}")
    out = (result.stdout or b"").decode("utf-8", errors="replace")
    return json.loads(out) if out else {}


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
        "raw": item,
    }


def search(subscription: Dict[str, Any]) -> List[Dict[str, Any]]:
    keyword = str(subscription.get("keyword") or "").strip()
    if not keyword:
        return []

    results = int((settings.PAYPAY or {}).get("results") or 100)
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
    headers = _headers()
    session = requests.Session()
    session.trust_env = False
    data: Dict[str, Any] = {}
    try:
        resp = session.get(url, headers=headers, timeout=(15, 45))
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        logger.info("PayPay 请求方式: requests")
    except _FALLBACK as exc:
        logger.warning("PayPay requests 失败，回退 curl: %s", exc)
        try:
            data = _curl_json(url, headers)
            logger.info("PayPay 请求方式: curl")
        except Exception as curl_exc:
            logger.warning("PayPay curl 也失败 keyword=%s: %s", keyword, curl_exc)
            raise
    except Exception as exc:
        logger.warning("PayPay 搜索异常 keyword=%s: %s", keyword, exc)
        raise

    items = data.get("items") or []
    out = [_normalize(it) for it in items if isinstance(it, dict) and it.get("id")]
    logger.info("PayPay 搜索完成: keyword=%s 返回=%s", keyword, len(out))
    return out
