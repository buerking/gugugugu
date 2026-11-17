"""冷却期：对 VIP 新货跑 AI，再邮件/企微推送；成功后写 seen。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src import settings
from src.pipeline.seen_db import SeenDB
from src.runtime_board import BOARD
from src.vip.big_bridge import use_big_src


logger = logging.getLogger(__name__)


def deliver_vip_pending(
    pending: List[Dict[str, Any]],
    *,
    first_run: bool = False,
) -> Dict[str, Any]:
    if first_run or not pending:
        return {"ok": True, "customers": 0, "pushed_items": 0, "skipped": True}

    # 按客户聚合（多关键词共用一次 AI + 一封邮件）
    by_user: Dict[str, Dict[str, Any]] = {}
    for row in pending:
        job = row.get("job")
        uid = str(getattr(job, "sub_user_id", "") or row.get("scope_id") or "")
        bucket = by_user.setdefault(
            uid,
            {
                "sub": dict(getattr(job, "raw", None) or {}),
                "reference_key": str(getattr(job, "reference_key", "") or "default"),
                "fresh": [],
                "scopes": [],
                "board_sids": [],
                "cache_keys": [],
                "search_ok": True,
            },
        )
        if getattr(job, "raw", None):
            bucket["sub"] = dict(job.raw)
        bucket["reference_key"] = str(
            getattr(job, "reference_key", None)
            or bucket["reference_key"]
            or "default"
        )
        bucket["fresh"].extend(list(row.get("fresh") or []))
        bucket["scopes"].append(str(row.get("scope_id") or ""))
        bucket["board_sids"].append(str(row.get("board_sid") or ""))
        bucket["cache_keys"].append(str(row.get("cache_key") or ""))
        if not row.get("search_ok", True):
            bucket["search_ok"] = False

    seen = SeenDB(settings.SEEN_DB_PATH)
    pushed_total = 0
    customers = 0

    # 商品去重
    for uid, bucket in by_user.items():
        uniq = []
        seen_ids = set()
        for it in bucket["fresh"]:
            key = f"{it.get('source_site')}:{it.get('id')}"
            if not it.get("id") or key in seen_ids:
                continue
            seen_ids.add(key)
            uniq.append(it)
        bucket["fresh"] = uniq

    try:
        with use_big_src():
            from src.ai.deepseek_filter import filter_items  # type: ignore
            from src.notify.dispatcher import push_items  # type: ignore

            for uid, bucket in by_user.items():
                customers += 1
                fresh = bucket["fresh"]
                sub = bucket["sub"] or {
                    "sub_user_id": uid,
                    "user_email": "",
                    "notify_channels": ["email"],
                    "customer_name": uid,
                }
                for bsid in bucket["board_sids"]:
                    if bsid:
                        BOARD.set_job(bsid, status="AI审核中", fresh_count=len(fresh))

                if not fresh:
                    for bsid in bucket["board_sids"]:
                        if bsid:
                            BOARD.set_job(bsid, status="已结束", reported_count=0)
                    continue

                limit = int(
                    ((getattr(settings, "VIP", None) or {}).get("ai_max_items_per_user"))
                    or 40
                )
                if limit and len(fresh) > limit:
                    fresh = fresh[:limit]

                passed, rejected = filter_items(
                    fresh, reference_key=str(bucket["reference_key"] or "default")
                )

                if passed:
                    try:
                        push_items(sub, passed)
                        pushed_total += len(passed)
                    except Exception as exc:
                        logger.warning("VIP推送失败 %s: %s", uid, exc)
                        for bsid in bucket["board_sids"]:
                            if bsid:
                                BOARD.set_job(
                                    bsid, status="异常", error=str(exc)[:300]
                                )
                        continue

                pushed_ids = {
                    f"{p.get('source_site')}:{p.get('id')}" for p in passed
                }
                for scope in bucket["scopes"]:
                    if not scope:
                        continue
                    # mark_seen 的 pushed 标志：仅对通过项
                    seen.mark_seen(
                        scope,
                        fresh,
                        keyword=str((fresh[0] or {}).get("matched_keyword") or ""),
                        pushed=False,
                    )
                    if pushed_ids:
                        passed_only = [
                            it
                            for it in fresh
                            if f"{it.get('source_site')}:{it.get('id')}" in pushed_ids
                        ]
                        if passed_only:
                            seen.mark_seen(
                                scope,
                                passed_only,
                                keyword=str(
                                    (passed_only[0] or {}).get("matched_keyword") or ""
                                ),
                                pushed=True,
                            )

                import time as _time

                now_ts = int(_time.time())
                for ck, ok in zip(
                    bucket["cache_keys"],
                    [bucket["search_ok"]] * len(bucket["cache_keys"]),
                ):
                    if ok and ck:
                        seen.set_watermark(ck, now_ts)

                for bsid in bucket["board_sids"]:
                    if bsid:
                        BOARD.set_job(
                            bsid,
                            status="已结束",
                            reported_count=len(passed),
                            fresh_count=len(fresh),
                        )
    except Exception as exc:
        logger.warning("VIP阶段失败: %s", exc)
        for row in pending:
            bsid = str(row.get("board_sid") or "")
            if bsid:
                BOARD.set_job(bsid, status="异常", error=str(exc)[:300])
        return {"ok": False, "error": str(exc), "customers": customers, "pushed_items": pushed_total}

    logger.info(
        "VIP摘要 customers=%s pushed=%s",
        customers,
        pushed_total,
        extra={"gui_console": True},
    )
    return {
        "ok": True,
        "customers": customers,
        "pushed_items": pushed_total,
        "skipped": False,
    }
