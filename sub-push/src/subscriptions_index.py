"""
订阅加载与索引。

正式契约：v3 = 订阅 list（无用户信息），见 config/api_contracts/subscriptions.v3.json。
兼容：v2 按用户嵌套、flat list、独立卖家文件（本地占位联调用）。
统一展开为 SearchJob；去重/上报以 subscription_id 为键。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 顶层绑定，避免 VIP use_big_src 清空 sys.modules 后懒加载失败
from src.vip.big_bridge import hold_subpush_src  # noqa: E402


@dataclass
class SearchJob:
    """一次接口调用单元：订阅 × 站点。"""

    job_type: str  # keyword | seller
    subscription_id: str
    keyword: str = ""
    sources: List[str] = field(default_factory=lambda: ["mercari", "paypay"])
    price_min: Any = ""
    price_max: Any = ""
    category_id: Any = ""
    brand_id: Any = ""
    exclude_keyword: str = ""
    created_time: Any = 0  # 后端 startTime / 本地 created_time
    seller_id: str = ""
    web_id: str = "mercari"  # 内部路由：mercari / paypay / yahoo_auction
    backend_web_id: int = 0  # 后端数字 webId，回传原样用
    mercari_as_shop: bool = False  # 煤炉商城 / 非数字店铺
    # backend=推线上接口；vip=大客户（AI+邮件），共用搜索/seen
    delivery: str = "backend"
    reference_key: str = ""
    customer_name: str = ""
    # 以下仅兼容本地旧 JSON / 旧处理器，不参与后端上报
    sub_user_id: str = ""
    user_email: str = ""
    user_phone: Any = ""
    notify_channels: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def web_name(self) -> str:
        """兼容旧代码读 web_name。"""
        return self.web_id

    def cache_key(self) -> str:
        if self.subscription_id:
            return f"sid:{self.subscription_id}"
        if self.job_type == "seller":
            return f"seller:{self.web_id}:{self.seller_id}"
        return f"kw:{self.keyword}|{self.sub_user_id}"


def resolve_subscription_id(
    row: Dict[str, Any],
    *,
    job_type: str,
    keyword: str = "",
    seller_id: str = "",
    web_id: str = "",
    sub_user_id: str = "",
) -> str:
    """优先显式 subscription_id；本地无 ID 时生成稳定伪 ID。"""
    for key in ("subscription_id", "sub_id", "record_id", "id"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        s = str(raw).strip()
        if key == "id" and s.isdigit() and len(s) < 6:
            continue
        return s

    if job_type == "seller":
        basis = f"seller|{sub_user_id}|{web_id}|{seller_id}"
    else:
        basis = (
            f"kw|{sub_user_id}|{keyword}|"
            f"{row.get('category_id') or ''}|"
            f"{row.get('brand_id') or ''}|"
            f"{row.get('price_min') or ''}|"
            f"{row.get('price_max') or ''}"
        )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"sub_{digest}"


def _resolve_web_id(row: Dict[str, Any], default: str = "mercari") -> str:
    for key in ("web_id", "web_name", "site_id", "source"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        return str(raw).strip().lower() or default
    return default


@dataclass
class UserProfile:
    """兼容旧日志统计；正式 v3 可无用户。"""

    sub_user_id: str
    user_email: str = ""
    user_phone: Any = ""
    job_count: int = 0


def _as_sources(raw: Any, default: Optional[List[str]] = None) -> List[str]:
    default = default or ["mercari", "paypay"]
    if isinstance(raw, list) and raw:
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [x.strip().lower() for x in raw.split(",") if x.strip()]
    return list(default)


def _as_notify_channels(raw: Any) -> List[str]:
    if isinstance(raw, list) and raw:
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def _jobs_from_flat_keyword(rows: List[Dict[str, Any]], default_sources: List[str]) -> List[SearchJob]:
    jobs: List[SearchJob] = []
    for row in rows:
        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        uid = str(row.get("sub_user_id") or "").strip()
        sid = resolve_subscription_id(
            row, job_type="keyword", keyword=kw, sub_user_id=uid
        )
        jobs.append(
            SearchJob(
                job_type="keyword",
                subscription_id=sid,
                keyword=kw,
                sources=_as_sources(row.get("sources"), default_sources),
                price_min=row.get("price_min") or "",
                price_max=row.get("price_max") or "",
                category_id=row.get("category_id") or "",
                brand_id=row.get("brand_id") or "",
                exclude_keyword=str(row.get("exclude_keyword") or ""),
                created_time=row.get("created_time") or 0,
                sub_user_id=uid,
                user_email=str(row.get("user_email") or ""),
                user_phone=row.get("user_phone") or "",
                notify_channels=_as_notify_channels(row.get("notify_channels")),
                raw=row,
            )
        )
    return jobs


def _jobs_from_v3(doc: Dict[str, Any], default_sources: List[str]) -> List[SearchJob]:
    """
    v3:
    {
      "version": 3,
      "subscriptions": [
        {"subscription_id":"...", "match_type":"keyword", "keyword":"...", "sources":[...]},
        {"subscription_id":"...", "match_type":"seller", "web_id":"mercari", "seller_id":"..."}
      ]
    }
    """
    jobs: List[SearchJob] = []
    for row in doc.get("subscriptions") or []:
        if not isinstance(row, dict):
            continue
        match_type = str(row.get("match_type") or "").strip().lower()
        if not match_type:
            if row.get("seller_id"):
                match_type = "seller"
            else:
                match_type = "keyword"

        if match_type == "seller":
            seller_id = str(row.get("seller_id") or "").strip()
            if not seller_id:
                continue
            web = _resolve_web_id(row)
            sid = resolve_subscription_id(
                row, job_type="seller", seller_id=seller_id, web_id=web
            )
            jobs.append(
                SearchJob(
                    job_type="seller",
                    subscription_id=sid,
                    keyword=f"seller_{web}_{seller_id}",
                    sources=[web],
                    created_time=row.get("created_time") or 0,
                    seller_id=seller_id,
                    web_id=web,
                    raw=row,
                )
            )
            continue

        kw = str(row.get("keyword") or "").strip()
        if not kw:
            continue
        sid = resolve_subscription_id(row, job_type="keyword", keyword=kw)
        jobs.append(
            SearchJob(
                job_type="keyword",
                subscription_id=sid,
                keyword=kw,
                sources=_as_sources(row.get("sources"), default_sources),
                price_min=row.get("price_min") or "",
                price_max=row.get("price_max") or "",
                category_id=row.get("category_id") or "",
                brand_id=row.get("brand_id") or "",
                exclude_keyword=str(row.get("exclude_keyword") or ""),
                created_time=row.get("created_time") or 0,
                raw=row,
            )
        )
    return jobs


def _jobs_from_v2(doc: Dict[str, Any], default_sources: List[str]) -> List[SearchJob]:
    """兼容旧 v2（按用户嵌套）；正式接口请用 v3。"""
    jobs: List[SearchJob] = []
    for user in doc.get("users") or []:
        if not isinstance(user, dict):
            continue
        uid = str(user.get("sub_user_id") or "").strip()
        email = str(user.get("user_email") or "")
        phone = user.get("user_phone") or ""
        channels = _as_notify_channels(user.get("notify_channels"))
        for kw_row in user.get("keywords") or []:
            if isinstance(kw_row, str):
                kw_row = {"keyword": kw_row}
            if not isinstance(kw_row, dict):
                continue
            kw = str(kw_row.get("keyword") or "").strip()
            if not kw:
                continue
            sid = resolve_subscription_id(
                kw_row, job_type="keyword", keyword=kw, sub_user_id=uid
            )
            jobs.append(
                SearchJob(
                    job_type="keyword",
                    subscription_id=sid,
                    keyword=kw,
                    sources=_as_sources(
                        kw_row.get("sources") or user.get("sources"),
                        default_sources,
                    ),
                    price_min=kw_row.get("price_min", user.get("price_min") or ""),
                    price_max=kw_row.get("price_max", user.get("price_max") or ""),
                    category_id=kw_row.get("category_id") or "",
                    brand_id=kw_row.get("brand_id") or "",
                    exclude_keyword=str(kw_row.get("exclude_keyword") or ""),
                    created_time=kw_row.get("created_time") or user.get("created_time") or 0,
                    sub_user_id=uid,
                    user_email=email,
                    user_phone=phone,
                    notify_channels=channels,
                    raw={**user, **kw_row},
                )
            )
        for srow in user.get("sellers") or []:
            if not isinstance(srow, dict):
                continue
            seller_id = str(srow.get("seller_id") or "").strip()
            if not seller_id:
                continue
            web = _resolve_web_id(srow)
            sid = resolve_subscription_id(
                srow,
                job_type="seller",
                seller_id=seller_id,
                web_id=web,
                sub_user_id=uid,
            )
            jobs.append(
                SearchJob(
                    job_type="seller",
                    subscription_id=sid,
                    keyword=f"seller_{web}_{seller_id}",
                    sources=[web],
                    created_time=srow.get("created_time") or user.get("created_time") or 0,
                    seller_id=seller_id,
                    web_id=web,
                    sub_user_id=uid,
                    user_email=email,
                    user_phone=phone,
                    notify_channels=channels,
                    raw={**user, **srow},
                )
            )
    return jobs


def jobs_from_backend_rows(rows: List[Dict[str, Any]]) -> List[SearchJob]:
    """
    后端 get-search-subscriptions 的 list → SearchJob。
    一条订阅只对应一个 webId。
    """
    from src.web_sites import is_mercari_shop_seller, parse_web_id, source_for_web_id

    jobs: List[SearchJob] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid_raw = row.get("subscriptionId", row.get("subscription_id"))
        if sid_raw is None or sid_raw == "":
            continue
        sid = str(sid_raw).strip()
        match_type = row.get("matchType", row.get("match_type"))
        try:
            mt = int(match_type)
        except Exception:
            mt = 1 if not row.get("sellerId") else 2
        backend_wid = parse_web_id(row.get("webId", row.get("web_id"))) or 28
        source = source_for_web_id(backend_wid)
        start_time = row.get("startTime", row.get("start_time") or 0)
        price_min = row.get("priceMin", row.get("price_min") or 0)
        price_max = row.get("priceMax", row.get("price_max") or 0)

        if mt == 2:
            seller_id = str(row.get("sellerId") or row.get("seller_id") or "").strip()
            if not seller_id:
                continue
            jobs.append(
                SearchJob(
                    job_type="seller",
                    subscription_id=sid,
                    keyword=f"seller_{source}_{seller_id}",
                    sources=[source],
                    price_min=0,
                    price_max=0,
                    created_time=start_time,
                    seller_id=seller_id,
                    web_id=source,
                    backend_web_id=backend_wid,
                    mercari_as_shop=is_mercari_shop_seller(backend_wid, seller_id),
                    raw=row,
                )
            )
            continue

        keyword = str(row.get("keyword") or "").strip()
        if not keyword:
            continue
        jobs.append(
            SearchJob(
                job_type="keyword",
                subscription_id=sid,
                keyword=keyword,
                sources=[source],
                price_min=price_min,
                price_max=price_max,
                created_time=start_time,
                web_id=source,
                backend_web_id=backend_wid,
                raw=row,
            )
        )
    return jobs


def load_search_jobs(
    *,
    default_sources: Optional[List[str]] = None,
    include_sellers: bool = True,
    include_vip: bool = True,
    from_middle: bool = False,
) -> tuple[List[SearchJob], Dict[str, UserProfile], Dict[str, Any]]:
    """
    公共订阅（接口或中间层）+ 可选大客户 VIP。
    正式调度请走中间层切片；本函数仍可供 GUI/兼容入口使用。
    """
    from src.vip.loader import load_vip_jobs

    if from_middle:
        from src.subscription_middle import get_middle

        middle = get_middle()
        middle.ensure_ready()
        jobs = list(middle.all_jobs())
        pub_n = len(jobs)
        fmt = "middle_layer"
    else:
        with hold_subpush_src():
            from src.pipeline import jpkix_api

            rows = jpkix_api.fetch_all_subscriptions()
            jobs = jobs_from_backend_rows(rows)
            for j in jobs:
                j.delivery = "backend"
            pub_n = len(jobs)
            fmt = "backend_api"

    vip_jobs: List[SearchJob] = []
    if include_vip:
        # from_middle 且已 mix 时可能重复；兼容入口保持原行为
        vip_jobs = load_vip_jobs(default_sources=default_sources)
        if not from_middle:
            jobs.extend(vip_jobs)
            if vip_jobs:
                fmt = f"{fmt}+vip"
        elif vip_jobs and "+vip" not in fmt:
            fmt = f"{fmt}+vip"
    meta: Dict[str, Any] = {
        "format": fmt,
        "subscription_count": pub_n,
        "vip_count": len(vip_jobs),
    }
    return jobs, {}, meta
