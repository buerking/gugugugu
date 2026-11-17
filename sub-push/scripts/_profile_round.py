# -*- coding: utf-8 -*-
"""拆一轮搜索各单元耗时（不写后端、不跑 VIP AI）。"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src.pipeline.search_engine import (  # noqa: E402
    plan_worker_queues,
    _run_keyword_source_task,
    _run_seller_task,
    _split_into_queues,
)
from src.subscriptions_index import load_search_jobs  # noqa: E402


def main() -> None:
    jobs, _, meta = load_search_jobs()
    created_after = str(int(time.time()) - 3600)
    units: List[Tuple[str, Any]] = []
    for job in jobs:
        if job.job_type == "seller":
            units.append(("seller", job))
            continue
        for src in job.sources:
            src = str(src).strip().lower()
            if src == "yahoo":
                src = "yahoo_auction"
            units.append(("keyword_source", (job, src)))

    layout = plan_worker_queues(len(units))
    print(f"jobs={len(jobs)} units={len(units)} meta={meta} pool={layout}")

    def run_one(kind: str, payload: Any) -> Dict[str, Any]:
        t0 = time.time()
        if kind == "seller":
            job = payload
            key, source, items, err, api_n = _run_seller_task(job, created_after)
            label = f"seller:{job.seller_id}"
        else:
            job, src = payload
            key, source, items, err, api_n = _run_keyword_source_task(
                job, src, created_after
            )
            label = f"{src}:{job.keyword}"
        return {
            "label": label[:60],
            "source": source,
            "api_n": api_n,
            "kept": len(items or []),
            "err": err or "",
            "sec": round(time.time() - t0, 2),
        }

    t_all = time.time()
    results: List[Dict[str, Any]] = []
    if layout["serial"] or layout["workers"] <= 1:
        for kind, payload in units:
            results.append(run_one(kind, payload))
    else:
        queues = _split_into_queues(units, int(layout["workers"]))
        with ThreadPoolExecutor(max_workers=int(layout["workers"])) as pool:
            futs = []
            for q in queues:
                for kind, payload in q:
                    futs.append(pool.submit(run_one, kind, payload))
            for fut in as_completed(futs):
                results.append(fut.result())

    wall = time.time() - t_all
    results.sort(key=lambda x: -x["sec"])
    print(f"\nwall={wall:.1f}s  sum={sum(r['sec'] for r in results):.1f}s")
    print("\nTop slow:")
    for r in results[:12]:
        flag = " ERR" if r["err"] else ""
        print(
            f"  {r['sec']:6.1f}s  api={r['api_n']:3d} kept={r['kept']:3d}  "
            f"{r['source']:14s} {r['label']}{flag}"
        )
    by_src: Dict[str, List[float]] = {}
    for r in results:
        by_src.setdefault(r["source"], []).append(r["sec"])
    print("\nBy source:")
    for src, secs in sorted(by_src.items()):
        print(
            f"  {src}: n={len(secs)} sum={sum(secs):.1f}s "
            f"avg={sum(secs)/len(secs):.1f}s max={max(secs):.1f}s"
        )


if __name__ == "__main__":
    main()
