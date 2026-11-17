import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src import settings
from src.ai.deepseek_filter import filter_items
from src.filters.time_window import build_created_after, item_in_time_window
from src.filters.title_filter import filter_items_by_keyword
from src.notify import dispatcher
from src.pipeline import state
from src.sources import mercari, paypay, yahoo_auction


logger = logging.getLogger(__name__)


def load_subscriptions() -> List[Dict[str, Any]]:
    with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("enabled", True)]


def _keywords_of(sub: Dict[str, Any]) -> List[str]:
    raw = sub.get("keywords")
    if isinstance(raw, list) and raw:
        out = []
        seen = set()
        for k in raw:
            s = str(k or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out
    one = str(sub.get("keyword") or "").strip()
    return [one] if one else []


def _sub_key(sub: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(sub.get("sub_user_id") or sub.get("customer_name") or ""),
            ",".join(_keywords_of(sub)),
        ]
    )


def collect_items_for_keyword(sub: Dict[str, Any], keyword: str) -> Tuple[List[Dict[str, Any]], bool]:
    """
    按关键词拉取各源。
    返回 (items, all_ok)：启用的源全部成功才 all_ok=True；任一源异常则 False（水位不推进）。
    """
    wanted = [str(x).lower() for x in (sub.get("sources") or ["mercari", "paypay", "yahoo_auction"])]
    probe = dict(sub)
    probe["keyword"] = keyword
    items: List[Dict[str, Any]] = []
    attempted = 0
    failed = 0

    if "mercari" in wanted and settings.MERCARI_ENABLED:
        attempted += 1
        try:
            items.extend(mercari.search(probe))
        except Exception as exc:
            failed += 1
            # 网络抖动常见：只打一行，不刷整段 Traceback
            logger.warning("Mercari 失败 keyword=%s: %s", keyword, exc)

    if "paypay" in wanted and settings.PAYPAY_ENABLED:
        attempted += 1
        try:
            items.extend(paypay.search(probe))
        except Exception as exc:
            failed += 1
            logger.warning("PayPay 失败 keyword=%s: %s", keyword, exc)

    if "yahoo_auction" in wanted and settings.YAHOO_AUCTION_ENABLED:
        attempted += 1
        try:
            items.extend(yahoo_auction.search(probe))
        except Exception as exc:
            failed += 1
            logger.warning("Yahoo Auction 失败 keyword=%s: %s", keyword, exc)

    for it in items:
        it["matched_keyword"] = keyword
    all_ok = attempted > 0 and failed == 0
    return items, all_ok


def collect_items(sub: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """合并多关键词结果；全部关键词下各源均成功才视为本轮搜索成功。"""
    keywords = _keywords_of(sub)
    merged: List[Dict[str, Any]] = []
    seen_ids = set()
    all_ok = True
    if not keywords:
        return [], False
    for kw in keywords:
        batch, ok = collect_items_for_keyword(sub, kw)
        if not ok:
            all_ok = False
        for it in batch:
            sid = f"{it.get('source_site')}:{it.get('id')}"
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            merged.append(it)
    return merged, all_ok


def _prefilter(
    sub: Dict[str, Any],
    items: List[Dict[str, Any]],
    *,
    first_run: bool,
    last_success_at: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """时间窗 + 在售/型号/整机启发式，减少送进 AI 的噪声。"""
    threshold = build_created_after(
        settings.SCHEDULE_INTERVAL_MINUTES,
        first_run,
        sub,
        last_success_at=None if first_run else last_success_at,
    )
    timed = [it for it in items if item_in_time_window(it, threshold)]
    dropped_time = len(items) - len(timed)
    if dropped_time or last_success_at:
        logger.info(
            "时间窗过滤: 丢掉=%s 保留=%s threshold=%s last_success_at=%s",
            dropped_time,
            len(timed),
            threshold,
            last_success_at,
        )

    keywords = _keywords_of(sub)
    primary_kw = keywords[0] if keywords else str(sub.get("keyword") or "")
    kept, dropped_title = filter_items_by_keyword(
        primary_kw,
        timed,
        reference_key=str(sub.get("reference_key") or ""),
    )
    if dropped_title:
        logger.info("标题/状态预过滤: 丢掉=%s 保留=%s", dropped_title, len(kept))
    return kept


def _save_report(sub: Dict[str, Any], passed: List[Dict[str, Any]], rejected: List[Dict[str, Any]]) -> str:
    report_dir = settings.abs_path(str((settings.TEST or {}).get("save_report_dir") or "data/reports"))
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    uid = str(sub.get("sub_user_id") or "user")
    path = os.path.join(report_dir, f"{uid}_{stamp}.json")
    payload = {
        "customer_name": sub.get("customer_name"),
        "sub_user_id": sub.get("sub_user_id"),
        "user_email": sub.get("user_email"),
        "keywords": _keywords_of(sub),
        "reference_key": sub.get("reference_key"),
        "passed": passed,
        "rejected": rejected,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def process_subscription(
    sub: Dict[str, Any],
    *,
    seen_map: Dict[str, list],
    first_run: bool,
    force_notify: bool = False,
    ai_limit: Optional[int] = None,
) -> int:
    keywords = _keywords_of(sub)
    logger.info(
        "处理订阅: customer=%s keywords=%s",
        sub.get("customer_name"),
        keywords,
    )

    key = _sub_key(sub)
    last_success_at = state.get_last_success_at(key, seen_map)
    started_at = int(time.time())

    raw_items, search_ok = collect_items(sub)
    if not search_ok:
        logger.warning(
            "本轮搜索未全部成功，不推进 last_success_at（当前水位=%s），下次仍从该水位之后补抓",
            last_success_at,
        )
    else:
        state.set_last_success_at(key, started_at, seen_map=seen_map)
        logger.info("搜索成功，更新 last_success_at=%s", started_at)

    # 强制推送测试时放宽时间窗（等同首轮 24h），避免 1.5×间隔把结果滤空
    candidates = _prefilter(
        sub,
        raw_items,
        first_run=(first_run or force_notify),
        last_success_at=last_success_at,
    )

    if first_run and not force_notify:
        state.mark_seen(key, candidates, seen_map=seen_map)
        logger.info("首轮建基线: 收录=%s（不推送） raw=%s", len(candidates), len(raw_items))
        return 0

    if force_notify:
        fresh = candidates
    else:
        fresh = state.filter_unseen(key, candidates, seen_map=seen_map)

    if not fresh:
        logger.info("无新商品: customer=%s", sub.get("customer_name"))
        return 0

    limit = ai_limit
    if limit is None:
        limit = int((settings.TEST or {}).get("ai_max_items_per_user") or 0) or None
    if limit and len(fresh) > limit:
        logger.info("AI 审核截断: %s -> %s", len(fresh), limit)
        fresh = fresh[:limit]

    passed, rejected = filter_items(fresh, reference_key=str(sub.get("reference_key") or "default"))
    for r in rejected[:15]:
        logger.info("AI拒绝: %s | %s", r.get("name"), r.get("ai_reason"))
    for p in passed[:10]:
        logger.info("AI通过: %s | %s", p.get("name"), p.get("ai_reason"))

    report = _save_report(sub, passed, rejected)
    logger.info("结果报告: %s", report)

    pushed_ids = {f"{p.get('source_site')}:{p.get('id')}" for p in passed}
    # 无论是否 force_notify，都写入已见记录，避免同一商品重复推送
    state.mark_seen(key, fresh, seen_map=seen_map, pushed_ids=pushed_ids)
    logger.info("已更新去重记录: key=%s 本轮见过=%s 其中推送=%s", key, len(fresh), len(pushed_ids))

    if not passed:
        logger.info("AI后无通过商品: customer=%s", sub.get("customer_name"))
        return 0

    try:
        dispatcher.push_items(sub, passed)
    except Exception as exc:
        logger.exception("推送失败（报告已保存）: %s", exc)
        return 0

    return len(passed)


def run_once(
    *,
    first_run: bool = False,
    force_notify: bool = False,
    ai_limit: Optional[int] = None,
) -> None:
    subs = load_subscriptions()
    if not subs:
        logger.warning("无启用的订阅")
        return

    seen_map = state.load_seen()
    total = 0
    for sub in subs:
        total += process_subscription(
            sub,
            seen_map=seen_map,
            first_run=first_run,
            force_notify=force_notify,
            ai_limit=ai_limit,
        )
    state.save_seen(seen_map)
    logger.info("本轮结束，推送商品合计=%s；去重文件已更新", total)
