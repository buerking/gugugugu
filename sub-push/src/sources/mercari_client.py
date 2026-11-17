import json
import logging
import hashlib
from typing import Dict, Any, List

import requests

from src import settings
from src.mapping import map_subscription_to_source_request


logger = logging.getLogger(__name__)


def create_sign(params: Dict[str, Any], secret: str) -> str:
    filtered = {k: v for k, v in params.items() if k not in ["Key", "sign"] and v not in ["", None, "null"]}
    sorted_items = dict(sorted(filtered.items()))
    sign_str = "&".join([f"{k}={v}" for k, v in sorted_items.items()]) + f"&key={secret}"
    sign_str = sign_str.replace("\\'", "'").replace('\\"', '"')
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


def build_condition(subscription: Dict[str, Any], created_after_date: str) -> Dict[str, str]:
    mapped = map_subscription_to_source_request("mercari", subscription, created_after_date)
    return {
        "keyword": str(mapped.get("keyword", "")),
        "category_id": str(mapped.get("category_id", "")),
        "created_after_date": str(mapped.get("created_after_date", "")),
        "brand_id": str(mapped.get("brand_id", "")),
        "price_min": str(mapped.get("price_min", "")),
        "price_max": str(mapped.get("price_max", "")),
    }


def search(subscription: Dict[str, Any], created_after_date: str) -> List[Dict[str, Any]]:
    condition = build_condition(subscription, created_after_date)
    params = {
        "type": "mercari",
        "app_id": "purchase_good_info",
        "pageIndex": 0,
        "pageSize": 100,
        "withAuction": "true",
        "condition": json.dumps(condition, separators=(",", ":")).replace("\\u", "u"),
    }
    sign = create_sign(params.copy(), settings.MERCARI_SECRET_KEY)
    condition_str = json.dumps(condition, separators=(",", ":"))
    url = (
        f"{settings.MERCARI_BASE_URL}"
        f"?r=api/request-transfer/product-page&type=mercari&condition={condition_str}"
        f"&pageIndex=0&pageSize=100&withAuction=true&sign={sign}&app_id=purchase_good_info"
    )
    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Success"):
            items = data.get("Data", {}).get("data", [])
            for item in items:
                item["source_site"] = "mercari"
            return items
        return []
    except Exception as exc:
        logger.error("Mercari 搜索失败: %s", exc)
        return []

