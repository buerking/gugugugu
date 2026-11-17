"""
后端 webId（数字）↔ 脚本内站点路由。

约定（与后端文档一致）：
- 28   煤炉：关键词/卖家合搜，一口价+拍卖同一套；类型看商品字段/详情
- 846  煤炉商城：卖家非纯数字 / 846 → shop；回传时商品 ID 非 m 开头 → 846
- 2733 煤炉代拍：与 28 同一套煤炉搜索；回传拍卖商品用 2733
- 324  PayPay 二手商城
- 988  雅虎拍卖
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# 后端数字 → 内部 source_site / 搜索路由
WEB_ID_TO_SOURCE: Dict[int, str] = {
    28: "mercari",
    846: "mercari",  # 商城仍走煤炉，卖家用 shop_id
    2733: "mercari",  # 代拍/拍卖不拆搜，与 28 相同
    324: "paypay",
    988: "yahoo_auction",
}

SOURCE_TO_DEFAULT_WEB_ID: Dict[str, int] = {
    "mercari": 28,
    "paypay": 324,
    "yahoo_auction": 988,
}

MERCARI_FAMILY_WEB_IDS = frozenset({28, 846, 2733})


def parse_web_id(raw: Any) -> Optional[int]:
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except Exception:
        return None


def source_for_web_id(web_id: Any) -> str:
    wid = parse_web_id(web_id)
    if wid is None:
        s = str(web_id or "").strip().lower()
        if s in ("mercari", "paypay", "yahoo_auction"):
            return s
        if s in ("yahoo", "auction", "yahoo_auc", "auctions"):
            return "yahoo_auction"
        if s in ("yahoo_fleamarket", "yahoo_fm"):
            return "paypay"
        return "mercari"
    return WEB_ID_TO_SOURCE.get(wid, "mercari")


def is_mercari_shop_seller(web_id: Any, seller_id: str) -> bool:
    """28 非纯数字 / 846 → 按 shop 查。"""
    wid = parse_web_id(web_id)
    sid = str(seller_id or "").strip()
    if not sid:
        return False
    if wid == 846:
        return True
    if wid == 28 and not sid.isdigit():
        return True
    return False


def _goods_code_of(item: Optional[Dict[str, Any]]) -> str:
    if not item:
        return ""
    return str(
        item.get("id") or item.get("goodsCode") or item.get("item_id") or ""
    ).strip()


def is_mercari_shop_goods(goods_code: str) -> bool:
    """煤炉商品 ID 不以 m 开头 → 煤炉商城（846）。"""
    g = str(goods_code or "").strip()
    if not g:
        return False
    return not g.lower().startswith("m")


def resolve_push_web_id(
    job_web_id: Any,
    source_site: str,
    *,
    item: Optional[Dict[str, Any]] = None,
) -> int:
    """
    回传 webId：按商品身份规范，不完全死跟订阅 webId。

    煤炉族：
    - goodsCode 非 m 开头 → 846（煤炉商城）
    - listing_type=auction → 2733（煤炉代拍）
    - 其余 m 开头一口价 → 28
    其它站：324 / 988；无 item 时回落订阅 webId 或站点默认。
    """
    site = str(source_site or "").strip().lower()
    job_wid = parse_web_id(job_web_id)

    if site in ("paypay", "yahoo_fleamarket", "yahoo_fm") or job_wid == 324:
        return 324
    if site in ("yahoo_auction", "yahoo", "auction") or job_wid == 988:
        return 988

    mercari_like = (
        site == "mercari"
        or job_wid in MERCARI_FAMILY_WEB_IDS
        or (job_wid is None and site in ("", "mercari"))
    )
    if mercari_like:
        goods = _goods_code_of(item)
        if goods and is_mercari_shop_goods(goods):
            return 846
        listing = str((item or {}).get("listing_type") or "").strip().lower()
        if listing == "auction":
            return 2733
        if goods:
            return 28
        # 尚无商品 ID：保留订阅标签（846 卖家搜 / 2733 代拍订）
        if job_wid in MERCARI_FAMILY_WEB_IDS:
            return int(job_wid)
        return 28

    if job_wid is not None:
        return int(job_wid)
    return int(SOURCE_TO_DEFAULT_WEB_ID.get(site or "mercari", 28))


def normalize_seller_id_for_compare(web_id: Any, seller_id: str) -> str:
    sid = str(seller_id or "").strip()
    wid = parse_web_id(web_id)
    # 雅虎拍卖比较不区分大小写
    if wid == 988 or source_for_web_id(web_id) == "yahoo_auction":
        return sid.lower()
    return sid
