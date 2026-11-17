"""
并发搜索引擎：把 SearchJob × source 拆成任务，默认 5 路并发，返回后按用户整合。
搜索源使用 big 同款直连接口（煤炉 entities:search + DPoP，非 jpkix 验签）。
"""

from __future__ import annotations

import datetime
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from src import settings
from src.keyword_title_filter import filter_items_by_keyword
from src.pipeline import backend_push
from src.pipeline.item_slim import maybe_gc, slim_item, slim_items
from src.pipeline.seen_db import SeenDB
from src.runtime_board import BOARD
from src.sources import mercari_web, paypay_web, yahoo_auction
from src.sources.mercari_seller_client import search_seller_new_listings
from src.sources import paypay_client
from src.subscriptions_index import SearchJob, load_search_jobs


logger = logging.getLogger(__name__)


def _board_sid(job: SearchJob) -> str:
    return str(job.subscription_id or job.cache_key())


def _time_window_cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "TIME_WINDOW", None) or {})


def _created_after_for_job(
    job: SearchJob,
    *,
    first_run: bool,
    seen: SeenDB,
    interval_minutes: int,
) -> str:
    """
    上架时间阈值（unix 秒）：
    - 首轮：订阅 created_time，否则近 24h
    - 常规：优先上次成功水位 − slack（进程卡死/停机后下一轮从水位补抓）
    - 无水位：now − interval×factor
    - 统一不低于 now − max_lookback（防止停机过久一次扫爆）
    各站搜索本身已按最新上架排序；此处再按时间窗裁剪。
    """
    now = int(time.time())
    tw = _time_window_cfg()
    slack = max(0, int(tw.get("watermark_slack_seconds") or 60))
    factor = float(tw.get("no_watermark_factor") or 1.5)
    max_lookback_h = float(tw.get("max_lookback_hours") or 24)
    floor = now - int(max(1.0, max_lookback_h) * 3600)

    if first_run:
        try:
            base = int(float(job.created_time or 0))
        except Exception:
            base = 0
        if base <= 0:
            base = now - 86400
        return str(max(base, floor))

    wm = seen.get_watermark(job.cache_key())
    if wm:
        # 卡死补回：从上次成功点往前留 slack，再被 max_lookback 托底
        threshold = int(wm) - slack
    else:
        threshold = now - int(interval_minutes * 60 * factor)
    threshold = max(threshold, floor)
    # 后端 startTime：早于此的不要回传；与水位取较新者
    try:
        start_time = int(float(job.created_time or 0))
    except Exception:
        start_time = 0
    if start_time > 0:
        threshold = max(threshold, start_time)
    return str(max(0, threshold))


def _item_ts(item: Dict[str, Any]) -> Optional[int]:
    """优先上架/开卖时间，其次更新时间；支持 unix 与 ISO8601。"""
    for key in ("created", "openTime", "updated", "startTime"):
        raw = item.get(key)
        if raw is None or raw == "":
            continue
        try:
            return int(float(raw))
        except Exception:
            pass
        try:
            s = str(raw).strip()
            return int(datetime.datetime.fromisoformat(s).timestamp())
        except Exception:
            continue
    return None


def _filter_by_created_after(items: List[Dict[str, Any]], created_after: str) -> List[Dict[str, Any]]:
    try:
        threshold = int(float(created_after))
    except Exception:
        threshold = 0
    drop_no_time = bool(_time_window_cfg().get("drop_items_without_time") or False)
    out: List[Dict[str, Any]] = []
    for it in items:
        ts = _item_ts(it)
        if ts is None:
            if not drop_no_time:
                out.append(it)
            continue
        if not threshold or ts >= threshold:
            out.append(it)
    # 按上架时间新→旧，便于后续截断/排查
    out.sort(key=lambda x: _item_ts(x) or 0, reverse=True)
    return out


def _search_source(source: str, sub_like: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = source.lower()
    if source == "mercari":
        return mercari_web.search(sub_like)
    if source == "paypay":
        return paypay_web.search(sub_like)
    if source in ("yahoo_auction", "yahoo"):
        return yahoo_auction.search(sub_like)
    raise ValueError(f"未知搜索源: {source}")


def _run_keyword_source_task(
    job: SearchJob,
    source: str,
    created_after: str,
) -> Tuple[str, str, List[Dict[str, Any]], Optional[str], int]:
    """返回 (cache_key, source, 时间窗内items, error, 接口原始条数)。"""
    sub_like = {
        "keyword": job.keyword,
        "price_min": job.price_min,
        "price_max": job.price_max,
        "category_id": job.category_id,
        "brand_id": job.brand_id,
        "exclude_keyword": job.exclude_keyword,
        "sub_user_id": job.sub_user_id,
    }
    try:
        items = _search_source(source, sub_like)
        api_n = len(items or [])
        items = _filter_by_created_after(items, created_after)
        return job.cache_key(), source, items, None, api_n
    except Exception as exc:
        logger.warning(
            "搜索失败 sid=%s kw=%s source=%s: %s",
            job.subscription_id,
            job.keyword,
            source,
            exc,
        )
        return job.cache_key(), source, [], str(exc), 0


def _run_seller_task(
    job: SearchJob,
    created_after: str,
) -> Tuple[str, str, List[Dict[str, Any]], Optional[str], int]:
    """卖家：先拉接口全量在售，再按时间窗裁；api_n=接口条数。"""
    web = str(job.web_id or job.web_name or "mercari").strip().lower()
    try:
        # created_after_date=0 → 不在客户端裁时间，保留接口真实条数
        if web in ("paypay", "yahoo", "yahoo_fleamarket", "yahoo_fm"):
            items = paypay_client.search_by_seller_id(
                seller_id=job.seller_id,
                created_after_date="0",
            )
            source = "paypay"
        elif web in ("yahoo_auction", "auction", "yahoo_auc", "auctions"):
            if not getattr(settings, "YAHOO_AUCTION_ENABLED", False):
                return job.cache_key(), "yahoo_auction", [], "yahoo_auction disabled", 0
            items = yahoo_auction.search_by_seller_id(
                seller_id=job.seller_id,
                created_after_date="0",
            )
            source = "yahoo_auction"
        else:
            items = search_seller_new_listings(
                seller_id=job.seller_id,
                created_after_date="0",
                limit=100,
                as_shop=bool(getattr(job, "mercari_as_shop", False)),
            )
            source = "mercari"
        api_n = len(items or [])
        items = _filter_by_created_after(items or [], created_after)
        return job.cache_key(), source, items, None, api_n
    except Exception as exc:
        logger.warning(
            "卖家搜索失败 sid=%s web=%s seller=%s: %s",
            job.subscription_id,
            web,
            job.seller_id,
            exc,
        )
        return job.cache_key(), web, [], str(exc), 0


def _enabled_sources() -> List[str]:
    out = []
    if getattr(settings, "MERCARI_ENABLED", True):
        out.append("mercari")
    if getattr(settings, "PAYPAY_ENABLED", True):
        out.append("paypay")
    if getattr(settings, "YAHOO_AUCTION_ENABLED", False):
        out.append("yahoo_auction")
    return out or ["mercari", "paypay"]


def _pool_cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "SEARCH_POOL", None) or {})


def plan_worker_queues(
    unit_count: int,
    *,
    per_worker_capacity: Optional[int] = None,
    min_workers: Optional[int] = None,
    max_workers: Optional[int] = None,
    serial_below_or_equal: Optional[int] = None,
) -> Dict[str, Any]:
    """
    弹性线程池规划：
    - 任务数 ≤ serial_below_or_equal：串行（不开线程池）
    - 否则每个 worker 固定容量 per_worker_capacity；超出则再开一个 worker
    - worker 数夹在 [min_workers, max_workers]
    """
    cfg = _pool_cfg()
    cap = max(1, int(per_worker_capacity or cfg.get("per_worker_capacity") or 5))
    mn = max(1, int(min_workers or cfg.get("min_workers") or 1))
    mx = max(mn, int(max_workers or cfg.get("max_workers") or 8))
    serial_n = max(0, int(serial_below_or_equal if serial_below_or_equal is not None else cfg.get("serial_below_or_equal") or 5))

    n = max(0, int(unit_count))
    if n == 0:
        return {
            "serial": True,
            "workers": 0,
            "capacity": cap,
            "queues": 0,
            "unit_count": 0,
        }
    if n <= serial_n:
        return {
            "serial": True,
            "workers": 1,
            "capacity": cap,
            "queues": 1,
            "unit_count": n,
        }
    # 每个队列容量 cap，需要的队列数 = ceil(n/cap)，再封顶 max_workers
    need = max(mn, int(math.ceil(n / float(cap))))
    workers = min(mx, need)
    return {
        "serial": False,
        "workers": workers,
        "capacity": cap,
        "queues": workers,
        "unit_count": n,
    }


def _is_paypay_unit(unit: Tuple[str, Any]) -> bool:
    """雅虎闲置（PayPay）单元：关键词 paypay 源，或卖家 web=paypay。"""
    kind, payload = unit
    if kind == "seller":
        job = payload
        web = str(job.web_id or job.web_name or "").strip().lower()
        return web in ("paypay", "yahoo", "yahoo_fleamarket", "yahoo_fm")
    if kind == "keyword_source":
        _job, src = payload
        return str(src).strip().lower() == "paypay"
    return False


def _zip_interleave(
    others: List[Tuple[str, Any]], paypays: List[Tuple[str, Any]]
) -> List[Tuple[str, Any]]:
    """排成 x雅虎 x雅虎 …（其他源与雅虎闲置交替，多余的接尾）。"""
    out: List[Tuple[str, Any]] = []
    i = j = 0
    while i < len(others) or j < len(paypays):
        if i < len(others):
            out.append(others[i])
            i += 1
        if j < len(paypays):
            out.append(paypays[j])
            j += 1
    return out


def interleave_paypay_units(
    units: List[Tuple[str, Any]],
) -> List[Tuple[str, Any]]:
    """单队列：把雅虎闲置与其它源岔开，形如 mercari/auction · paypay · …"""
    if len(units) <= 1:
        return list(units)
    others = [u for u in units if not _is_paypay_unit(u)]
    paypays = [u for u in units if _is_paypay_unit(u)]
    if not others or not paypays:
        return list(units)
    return _zip_interleave(others, paypays)


def _split_into_queues(
    units: List[Tuple[str, Any]], worker_count: int
) -> List[List[Tuple[str, Any]]]:
    """
    均分到 worker_count 条队列，且每条队列内尽量 x雅虎x雅虎 交替。
    先按源分桶再轮询灌入各 worker，避免简单轮询把 paypay 全堆到同一 worker。
    """
    w = max(1, int(worker_count))
    others = [u for u in units if not _is_paypay_unit(u)]
    paypays = [u for u in units if _is_paypay_unit(u)]
    other_qs: List[List[Tuple[str, Any]]] = [[] for _ in range(w)]
    pay_qs: List[List[Tuple[str, Any]]] = [[] for _ in range(w)]
    for i, unit in enumerate(others):
        other_qs[i % w].append(unit)
    for i, unit in enumerate(paypays):
        pay_qs[i % w].append(unit)
    return [_zip_interleave(other_qs[i], pay_qs[i]) for i in range(w)]


def _execute_unit(
    kind: str,
    payload: Any,
    created_after_map: Dict[str, str],
) -> Tuple[str, str, List[Dict[str, Any]], Optional[str], int]:
    if kind == "seller":
        job = payload
        return _run_seller_task(job, created_after_map[job.cache_key()])
    job, src = payload
    return _run_keyword_source_task(job, src, created_after_map[job.cache_key()])


def _run_queue_worker(
    queue_units: List[Tuple[str, Any]],
    created_after_map: Dict[str, str],
    worker_id: int,
) -> List[Tuple[str, str, List[Dict[str, Any]], Optional[str], int]]:
    """单线程消费自己的固定队列；返回前对商品列表瘦身。"""
    out: List[Tuple[str, str, List[Dict[str, Any]], Optional[str], int]] = []
    logger.info("worker-%s 启动，队列长度=%s", worker_id, len(queue_units))
    for kind, payload in queue_units:
        if kind == "seller":
            job = payload
            hint = f"seller={job.seller_id}"
        else:
            job, src = payload
            hint = f"kw={job.keyword} src={src}"
        bsid = _board_sid(job)
        t0 = time.time()
        logger.info("搜索开始 worker-%s sid=%s %s", worker_id, bsid, hint)
        cache_key, source, items, err, api_n = _execute_unit(
            kind, payload, created_after_map
        )
        elapsed = time.time() - t0
        kept = len(items or [])
        logger.info(
            "搜索结束 worker-%s sid=%s source=%s api=%s kept=%s 耗时=%.2fs err=%s",
            worker_id,
            bsid,
            source,
            int(api_n or 0),
            kept,
            elapsed,
            (err or "-")[:180],
        )
        try:
            BOARD.note_search_unit(
                bsid, source=source, api_n=int(api_n or 0), err=err
            )
        except Exception:
            pass
        out.append((cache_key, source, slim_items(items), err, int(api_n or 0)))
    logger.info("worker-%s 完成，处理=%s", worker_id, len(out))
    return out


def run_search_round(
    *,
    first_run: bool = False,
    jobs: Optional[List[SearchJob]] = None,
    ingest_meta: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    board_replace: bool = True,
    finalize_board: bool = True,
) -> Dict[str, Any]:
    """
    一整轮（或一片）：并发搜 → 标题过滤 → 本地去重 → 公共推后端 / VIP 待 AI。
    jobs 传入时不再打订阅接口（由中间层/VIP 线程供数）。
    """
    if getattr(settings, "AI_ENABLED", False):
        logger.warning("检测到 AI_ENABLED，但 sub-push 公共任务强制跳过 AI")

    interval = max(1, int(settings.SCHEDULE_INTERVAL_MINUTES))
    seen = SeenDB(settings.SEEN_DB_PATH)
    default_sources = _enabled_sources()
    t_round = time.time()
    users: Dict[str, Any] = {}
    if jobs is None:
        try:
            # 无注入 jobs 时只拉公共（VIP 默认走中间层混编）
            jobs, users, ingest_meta = load_search_jobs(
                default_sources=default_sources,
                include_sellers=True,
                include_vip=False,
            )
        except Exception as exc:
            BOARD.begin_round(f"run-{int(time.time())}", [], replace=board_replace)
            BOARD.end_round(error=str(exc), finalize_round=finalize_board)
            raise
    else:
        jobs = list(jobs)
        ingest_meta = dict(ingest_meta or {})
        if "format" not in ingest_meta:
            ingest_meta["format"] = "injected"

    rid = str(run_id or f"run-{int(time.time())}")
    ingest_format = str((ingest_meta or {}).get("format") or "")
    BOARD.begin_round(rid, jobs, replace=board_replace)
    job_sids = [
        str(getattr(j, "subscription_id", "") or "")
        for j in jobs
        if str(getattr(j, "subscription_id", "") or "")
    ]

    logger.info(
        "本轮任务: run_id=%s format=%s jobs=%s first_run=%s seen=%s",
        rid,
        ingest_format,
        len(jobs),
        first_run,
        seen.stats(),
    )

    try:
        return _run_search_round_body(
            jobs=jobs,
            users=users,
            ingest_format=ingest_format,
            run_id=rid,
            first_run=first_run,
            interval=interval,
            seen=seen,
            t_round=t_round,
            board_sids=job_sids,
            finalize_board=finalize_board,
        )
    except Exception as exc:
        BOARD.end_round(
            error=str(exc),
            only_sids=job_sids,
            finalize_round=finalize_board,
        )
        raise


def _run_search_round_body(
    *,
    jobs: List[SearchJob],
    users: Dict[str, Any],
    ingest_format: str,
    run_id: str,
    first_run: bool,
    interval: int,
    seen: SeenDB,
    t_round: float,
    board_sids: Optional[List[str]] = None,
    finalize_board: bool = True,
) -> Dict[str, Any]:
    round_err = ""
    only_sids = list(board_sids or [])
    # 展开为可并发单元
    unit_specs: List[Tuple[str, Any]] = []
    created_after_map: Dict[str, str] = {}
    job_by_key: Dict[str, SearchJob] = {}

    for job in jobs:
        job_by_key[job.cache_key()] = job
        ca = _created_after_for_job(
            job, first_run=first_run, seen=seen, interval_minutes=interval
        )
        created_after_map[job.cache_key()] = ca
        if job.job_type == "seller":
            unit_specs.append(("seller", job))
            continue
        for src in job.sources:
            src = str(src).strip().lower()
            if src == "yahoo":
                src = "yahoo_auction"
            if src == "mercari" and not settings.MERCARI_ENABLED:
                continue
            if src == "paypay" and not settings.PAYPAY_ENABLED:
                continue
            if src == "yahoo_auction" and not settings.YAHOO_AUCTION_ENABLED:
                continue
            if src not in ("mercari", "paypay", "yahoo_auction"):
                logger.warning("跳过未知源 %s (job=%s)", src, job.cache_key())
                continue
            unit_specs.append(("keyword_source", (job, src)))

    if created_after_map:
        sample_key = next(iter(created_after_map))
        logger.info(
            "时间窗: 并发单元=%s sample_key=%s created_after=%s (%s) max_lookback_h=%s",
            len(unit_specs),
            sample_key,
            created_after_map[sample_key],
            datetime.datetime.fromtimestamp(int(created_after_map[sample_key])).isoformat(
                timespec="seconds"
            ),
            _time_window_cfg().get("max_lookback_hours"),
        )
    else:
        logger.info("并发单元数: %s", len(unit_specs))

    # cache_key -> {source: items, errors: []}
    gathered: Dict[str, Dict[str, Any]] = {}

    def _ensure(key: str) -> Dict[str, Any]:
        if key not in gathered:
            gathered[key] = {"by_source": {}, "api_counts": {}, "errors": []}
        return gathered[key]

    gc_every_workers = max(
        1, int((getattr(settings, "MEMORY", None) or {}).get("gc_every_workers") or 1)
    )
    gc_every_jobs = max(
        1, int((getattr(settings, "MEMORY", None) or {}).get("gc_every_jobs") or 25)
    )

    def _ingest_results(
        rows: List[Tuple[str, str, List[Dict[str, Any]], Optional[str], int]]
    ) -> None:
        for cache_key, source, items, err, api_n in rows:
            bucket = _ensure(cache_key)
            # 再瘦身一次，防止未走 worker 瘦身的路径带上大字段
            bucket["by_source"][source] = slim_items(items)
            bucket["api_counts"][source] = int(api_n or 0)
            if err:
                bucket["errors"].append(f"{source}:{err}")

    n_paypay = sum(1 for u in unit_specs if _is_paypay_unit(u))
    layout = plan_worker_queues(len(unit_specs))
    logger.info(
        "搜索池规划: units=%s paypay=%s other=%s serial=%s workers=%s capacity=%s/worker",
        layout["unit_count"],
        n_paypay,
        layout["unit_count"] - n_paypay,
        layout["serial"],
        layout["workers"],
        layout["capacity"],
    )

    if not unit_specs:
        pass
    elif layout["serial"]:
        ordered = interleave_paypay_units(unit_specs)
        _ingest_results(
            _run_queue_worker(ordered, created_after_map, worker_id=0)
        )
        maybe_gc(force=True)
    else:
        queues = _split_into_queues(unit_specs, int(layout["workers"]))
        for qi, q in enumerate(queues):
            if not q:
                continue
            seq = []
            for kind, payload in q:
                if kind == "seller":
                    web = str(
                        payload.web_id or payload.web_name or "mercari"
                    ).strip().lower()
                    seq.append(f"seller:{web}")
                else:
                    seq.append(str(payload[1]))
            logger.info(
                "worker-%s 队列穿插: len=%s pattern=%s",
                qi,
                len(q),
                " ".join(seq[:24]) + (" …" if len(seq) > 24 else ""),
            )
        with ThreadPoolExecutor(max_workers=int(layout["workers"])) as pool:
            futures = [
                pool.submit(_run_queue_worker, q, created_after_map, i)
                for i, q in enumerate(queues)
                if q
            ]
            done_w = 0
            for fut in as_completed(futures):
                _ingest_results(fut.result())
                done_w += 1
                if done_w % gc_every_workers == 0:
                    maybe_gc(force=True)

    # 整合 → 过滤 → 去重 → 公共走后端上报；VIP 留给冷却期 AI+邮件
    results_push: Dict[str, Dict[str, Any]] = {}
    vip_pending: List[Dict[str, Any]] = []
    total_raw = 0
    total_fresh = 0
    success_jobs = 0
    pending_watermarks: Dict[str, bool] = {}  # cache_key -> 搜索是否全成功
    job_fresh_keys: Dict[str, List[str]] = {}  # cache_key -> 本轮待推 item_key（仅 backend）

    # subscription_id -> 本轮统计（推送后回填 reported）
    per_job_stats: Dict[str, Dict[str, Any]] = {}

    jobs_processed = 0
    for cache_key, job in job_by_key.items():
        scope_id = str(job.subscription_id or job.sub_user_id or cache_key)
        board_sid = _board_sid(job)
        BOARD.set_job(board_sid, status="运行中")
        bucket = gathered.pop(cache_key, None) or {
            "by_source": {},
            "api_counts": {},
            "errors": [],
        }
        search_ok = not bool(bucket.get("errors"))
        pending_watermarks[cache_key] = search_ok
        err_text = "; ".join(bucket.get("errors") or [])
        if not search_ok:
            logger.warning(
                "搜索有源失败，暂不推进水位 key=%s errors=%s",
                cache_key,
                bucket.get("errors"),
            )
        else:
            success_jobs += 1

        # 返回=各源接口原始命中合计（时间窗过滤前）
        api_n = int(sum(int(v or 0) for v in (bucket.get("api_counts") or {}).values()))
        merged: List[Dict[str, Any]] = []
        seen_ids = set()
        for src, items in (bucket.get("by_source") or {}).items():
            for it in items:
                sid = f"{it.get('source_site')}:{it.get('id')}"
                if not it.get("id") or sid in seen_ids:
                    continue
                seen_ids.add(sid)
                slim = slim_item(it)
                slim["matched_keyword"] = job.keyword
                merged.append(slim)
        bucket.clear()

        # 界面「返回」用接口条数；内部后续仍用时间窗后的 merged 做新货/上报
        raw_n = api_n if api_n > 0 else len(merged)
        total_raw += raw_n

        if job.job_type == "keyword" and job.keyword:
            merged, dropped = filter_items_by_keyword(job.keyword, merged)
            if dropped:
                logger.debug(
                    "标题过滤 sid=%s kw=%s 剔除=%s 保留=%s",
                    scope_id,
                    job.keyword,
                    dropped,
                    len(merged),
                )

        if first_run:
            seen.mark_seen(scope_id, merged, keyword=job.keyword, pushed=False)
            if search_ok:
                seen.set_watermark(cache_key, int(time.time()))
            BOARD.set_job(
                board_sid,
                status="已结束" if search_ok else "异常",
                raw_count=raw_n,
                fresh_count=0,
                reported_count=0,
                error=err_text,
            )
            per_job_stats[board_sid] = {"raw": raw_n, "fresh": 0, "reported": 0}
            merged.clear()
            jobs_processed += 1
            if jobs_processed % gc_every_jobs == 0:
                maybe_gc(force=True)
            continue

        fresh = seen.filter_unseen(scope_id, merged)
        fresh_n = len(fresh)
        if not fresh:
            if search_ok:
                seen.set_watermark(cache_key, int(time.time()))
            BOARD.set_job(
                board_sid,
                status="已结束" if search_ok else "异常",
                raw_count=raw_n,
                fresh_count=0,
                reported_count=0,
                error=err_text,
            )
            per_job_stats[board_sid] = {"raw": raw_n, "fresh": 0, "reported": 0}
            merged.clear()
            jobs_processed += 1
            if jobs_processed % gc_every_jobs == 0:
                maybe_gc(force=True)
            continue

        total_fresh += fresh_n
        is_vip = str(getattr(job, "delivery", "backend") or "backend") == "vip"
        if is_vip:
            vip_fresh = [slim_item(it) for it in fresh]
            vip_pending.append(
                {
                    "scope_id": scope_id,
                    "cache_key": cache_key,
                    "board_sid": board_sid,
                    "job": job,
                    "fresh": vip_fresh,
                    "search_ok": search_ok,
                    "raw_n": raw_n,
                }
            )
            BOARD.set_job(
                board_sid,
                status="待AI" if search_ok else "异常",
                raw_count=raw_n,
                fresh_count=fresh_n,
                reported_count=0,
                error=err_text,
            )
            per_job_stats[board_sid] = {
                "raw": raw_n,
                "fresh": fresh_n,
                "reported": 0,
                "scope_id": scope_id,
            }
            merged.clear()
            fresh.clear()
            jobs_processed += 1
            if jobs_processed % gc_every_jobs == 0:
                maybe_gc(force=True)
            continue

        job_fresh_keys[cache_key] = [
            f"{it.get('source_site')}:{it.get('id')}" for it in fresh
        ]
        bucket_push = results_push.setdefault(
            scope_id,
            {
                "items": [],
                "_fresh_raw": [],
                "_cache_keys": set(),
                "_board_sids": set(),
                "backend_web_id": int(getattr(job, "backend_web_id", 0) or 0),
            },
        )
        if not bucket_push.get("backend_web_id") and getattr(job, "backend_web_id", 0):
            bucket_push["backend_web_id"] = int(job.backend_web_id)
        bucket_push["_cache_keys"].add(cache_key)
        bucket_push["_board_sids"].add(board_sid)
        for it in fresh:
            slim = slim_item(it)
            bucket_push["_fresh_raw"].append(
                {
                    "id": slim.get("id"),
                    "source_site": slim.get("source_site"),
                    "name": slim.get("name"),
                    "price": slim.get("price"),
                    "matched_keyword": job.keyword,
                }
            )
            built = backend_push.build_item_payload(
                slim,
                keyword=job.keyword,
                web_id=getattr(job, "backend_web_id", 0) or None,
            )
            if built:
                bucket_push["items"].append(built)
        BOARD.set_job(
            board_sid,
            status="上报中" if search_ok else "异常",
            raw_count=raw_n,
            fresh_count=fresh_n,
            reported_count=0,
            error=err_text,
        )
        per_job_stats[board_sid] = {
            "raw": raw_n,
            "fresh": fresh_n,
            "reported": 0,
            "scope_id": scope_id,
        }
        merged.clear()
        fresh.clear()
        jobs_processed += 1
        if jobs_processed % gc_every_jobs == 0:
            maybe_gc(force=True)

    push_result: Dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "reason": "first_run" if first_run else "empty",
    }
    if not first_run and results_push:
        payload = backend_push.build_batch_payload(
            results_push,
            run_id=run_id,
            stats={
                "job_count": len(jobs),
                "raw_item_count": total_raw,
                "fresh_item_count": total_fresh,
                "subscription_count": len(results_push),
            },
            interval_minutes=interval,
        )
        try:
            push_result = backend_push.push_batch(payload)
            can_absorb = bool(push_result.get("ok")) and (
                not push_result.get("skipped")
                or push_result.get("dry_run")
                or push_result.get("reason") == "disabled"
            )
            if can_absorb:
                accepted = set(push_result.get("accepted_item_keys") or [])
                really_pushed = bool(
                    push_result.get("ok")
                    and not push_result.get("skipped")
                    and not push_result.get("dry_run")
                )
                for sid, info in results_push.items():
                    raw_items = list(info.get("_fresh_raw") or [])
                    if accepted:
                        raw_items = [
                            it
                            for it in raw_items
                            if f"{it.get('source_site')}:{it.get('id')}" in accepted
                        ]
                    reported_n = len(raw_items)
                    for bsid in info.get("_board_sids") or {sid}:
                        st = per_job_stats.get(str(bsid)) or {}
                        BOARD.set_job(
                            str(bsid),
                            status="已结束",
                            reported_count=reported_n,
                        )
                        st["reported"] = reported_n
                    if raw_items:
                        seen.mark_seen(
                            sid,
                            raw_items,
                            keyword=str(
                                (raw_items[0] or {}).get("matched_keyword") or ""
                            ),
                            pushed=really_pushed,
                        )
                now_ts = int(time.time())
                for cache_key, ok in pending_watermarks.items():
                    if ok and cache_key in job_fresh_keys:
                        seen.set_watermark(cache_key, now_ts)
            else:
                logger.warning(
                    "推送未吸收，不写 seen、不推进相关水位，下轮补推: %s",
                    push_result,
                )
                for sid, info in results_push.items():
                    for bsid in info.get("_board_sids") or {sid}:
                        BOARD.set_job(
                            str(bsid),
                            status="异常",
                            error=str(push_result.get("error") or push_result.get("reason") or "推送未吸收")[:300],
                        )
        except Exception as exc:
            logger.exception("后端推送失败（不写 seen / 不推水位，下轮补推）: %s", exc)
            push_result = {"ok": False, "error": str(exc)}
            round_err = str(exc)
            for sid, info in results_push.items():
                for bsid in info.get("_board_sids") or {sid}:
                    BOARD.set_job(str(bsid), status="异常", error=str(exc)[:300])

    # VIP 未结束前先不 finalize；切片模式也不把整轮标 done
    summary = {
        "run_id": run_id,
        "format": ingest_format,
        "jobs": len(jobs),
        "units": len(unit_specs),
        "pool": layout,
        "success_jobs": success_jobs,
        "raw_items": total_raw,
        "fresh_items": total_fresh,
        "push_subscriptions": len(results_push),
        "vip_pending_count": len(vip_pending),
        "vip_pending": vip_pending,
        "first_run": first_run,
        "ai": False,
        "push": push_result,
        "seen": seen.stats(),
        "elapsed_seconds": round(time.time() - t_round, 2),
    }
    if not vip_pending:
        BOARD.end_round(
            error=round_err,
            only_sids=only_sids or None,
            finalize_round=finalize_board,
        )
    logger.debug(
        "搜索轮结束 elapsed=%.1fs jobs=%s fresh=%s push=%s vip_pending=%s",
        summary["elapsed_seconds"],
        len(jobs),
        total_fresh,
        len(results_push),
        len(vip_pending),
    )

    gathered.clear()
    for info in results_push.values():
        info.pop("_fresh_raw", None)
        info.pop("_cache_keys", None)
        info.pop("_board_sids", None)
        info.pop("items", None)
    results_push.clear()
    job_fresh_keys.clear()
    pending_watermarks.clear()
    unit_specs.clear()
    created_after_map.clear()
    per_job_stats.clear()
    maybe_gc(force=True)

    return summary
