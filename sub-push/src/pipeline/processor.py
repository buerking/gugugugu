"""
兼容层：旧的「单条订阅串行搜索」仍可用；主流程请用 search_engine.run_search_round。
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from src import settings
from src.keyword_title_filter import filter_items_by_keyword
from src.pipeline import storage
from src.sources import mercari_web, paypay_web


logger = logging.getLogger(__name__)


def process_subscription(
    subscription: Dict[str, Any], created_after_date: str
) -> Optional[Tuple[str, str, int, str]]:
    """单条关键词订阅（串行，仅调试用）。正式调度走 search_engine 并发。"""
    user_id = subscription["sub_user_id"]
    keyword = str(subscription.get("keyword", ""))
    logger.info("开始处理订阅(兼容串行): user=%s keyword=%s", user_id, keyword)

    mercari_items = mercari_web.search(subscription) if settings.MERCARI_ENABLED else []
    paypay_items = paypay_web.search(subscription) if settings.PAYPAY_ENABLED else []

    # 时间窗
    try:
        threshold = int(float(created_after_date))
    except Exception:
        threshold = 0

    def _keep(it: Dict[str, Any]) -> bool:
        if not threshold:
            return True
        for k in ("created", "updated"):
            try:
                if int(float(it.get(k))) >= threshold:
                    return True
            except Exception:
                continue
        return True  # 无时间戳时保留

    mercari_items = [x for x in mercari_items if _keep(x)]
    paypay_items = [x for x in paypay_items if _keep(x)]
    raw_count = len(mercari_items) + len(paypay_items)
    merged_items: List[Dict[str, Any]] = mercari_items + paypay_items
    merged_items, title_dropped = filter_items_by_keyword(keyword, merged_items)
    if title_dropped:
        logger.info(
            "标题过滤: user=%s keyword=%s 接口返回=%s 保留=%s 剔除=%s",
            user_id,
            keyword,
            raw_count,
            len(merged_items),
            title_dropped,
        )
    if not merged_items:
        return None

    merged_result = {
        "Success": True,
        "Data": {
            "meta": {
                "num_found": len(merged_items),
                "num_mercari": len(mercari_items),
                "num_paypay": len(paypay_items),
                "num_raw": raw_count,
                "num_title_filtered_out": title_dropped,
            },
            "data": merged_items,
        },
    }
    storage.save_search_result(subscription, merged_result)
    return user_id, keyword, len(merged_items), subscription["user_email"]


def process_seller_subscription(
    seller_subscription: Dict[str, Any],
    created_after_date: str,
) -> Optional[Tuple[str, str, int, str]]:
    from src.sources.mercari_seller_client import search_seller_new_listings
    from src.sources import paypay_client

    user_id = str(seller_subscription.get("sub_user_id", ""))
    seller_id = str(seller_subscription.get("seller_id", ""))
    user_email = str(seller_subscription.get("user_email", ""))
    web_name = str(seller_subscription.get("web_name", "mercari")).strip().lower() or "mercari"
    if not user_id or not seller_id:
        return None

    keyword = f"seller_{web_name}_{seller_id}"
    logger.info("开始处理卖家订阅(兼容串行): user=%s web=%s seller_id=%s", user_id, web_name, seller_id)

    if web_name in ("paypay", "yahoo", "yahoo_fleamarket", "yahoo_fm"):
        items = paypay_client.search_by_seller_id(
            seller_id=seller_id, created_after_date=created_after_date
        )
        meta_key = "num_paypay_seller"
    elif web_name in ("yahoo_auction", "auction", "yahoo_auc", "auctions"):
        from src.sources import yahoo_auction

        items = yahoo_auction.search_by_seller_id(
            seller_id=seller_id, created_after_date=created_after_date
        )
        meta_key = "num_yahoo_auction_seller"
    else:
        items = search_seller_new_listings(
            seller_id=seller_id, created_after_date=created_after_date, limit=100
        )
        meta_key = "num_mercari_seller"
    if not items:
        return None

    merged_result = {
        "Success": True,
        "Data": {
            "meta": {"num_found": len(items), meta_key: len(items)},
            "data": items,
        },
    }
    storage.save_search_result(
        {
            "keyword": keyword,
            "sub_user_id": user_id,
            "user_email": user_email,
            "web_name": web_name,
        },
        merged_result,
    )
    return user_id, keyword, len(items), user_email


def build_created_after(
    schedule_interval_minutes: int, first_run: bool, subscription: Dict[str, Any]
) -> str:
    if first_run:
        return str(subscription.get("created_time") or 0)
    dt = datetime.datetime.now() - datetime.timedelta(
        minutes=(schedule_interval_minutes * 1.5)
    )
    return str(int(dt.timestamp()))
