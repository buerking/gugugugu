"""
循序渐进压测搜索并发，观察是否触发风控（423/429/超时/SSL）。

只用旧 config/subscriptions.json 关键词，不推后端、不跑 VIP/AI。
默认很克制：少量词 → 逐步加 worker → 每档之间冷却。
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _load_keywords(limit: int) -> List[str]:
    path = os.path.join(ROOT, "config", "subscriptions.json")
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    kws: List[str] = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kw = str(row.get("keyword") or "").strip()
        if not kw or kw.lower() in seen:
            continue
        seen.add(kw.lower())
        kws.append(kw)
        if len(kws) >= limit:
            break
    return kws


def _search_one(keyword: str, source: str) -> Dict[str, Any]:
    from src.subscriptions_index import SearchJob
    from src.pipeline.search_engine import _run_keyword_source_task

    job = SearchJob(
        job_type="keyword",
        subscription_id=f"probe:{source}:{keyword}",
        keyword=keyword,
        sources=[source],
        delivery="backend",
    )
    created_after = str(int(time.time()) - 3600 * 6)
    t0 = time.time()
    try:
        cache_key, src, items, err, api_n = _run_keyword_source_task(
            job, source, created_after
        )
        elapsed = time.time() - t0
        return {
            "keyword": keyword,
            "source": source,
            "ok": not bool(err),
            "error": err or "",
            "count": int(api_n or len(items or [])),
            "elapsed": round(elapsed, 2),
        }
    except Exception as exc:
        return {
            "keyword": keyword,
            "source": source,
            "ok": False,
            "error": str(exc)[:240],
            "count": 0,
            "elapsed": round(time.time() - t0, 2),
        }


def _run_wave(
    keywords: List[str],
    *,
    source: str,
    workers: int,
) -> Dict[str, Any]:
    units = [(kw, source) for kw in keywords]
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    if workers <= 1:
        for kw, src in units:
            results.append(_search_one(kw, src))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_search_one, kw, src) for kw, src in units]
            for fut in as_completed(futs):
                results.append(fut.result())
    elapsed = time.time() - t0
    ok_n = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    # 粗判风控信号
    risk_hits = []
    for r in fail:
        e = (r.get("error") or "").lower()
        if any(
            x in e
            for x in (
                "423",
                "429",
                "403",
                "401",
                "captcha",
                "blocked",
                "cloudflare",
                "ssl",
                "timed out",
                "timeout",
                "连接",
            )
        ):
            risk_hits.append(r)
    return {
        "workers": workers,
        "source": source,
        "keyword_count": len(keywords),
        "ok": ok_n,
        "fail": len(fail),
        "risk_like": len(risk_hits),
        "elapsed_seconds": round(elapsed, 2),
        "avg_item_seconds": round(elapsed / max(1, len(keywords)), 2),
        "failures": fail[:8],
        "risk_samples": risk_hits[:5],
    }


def main() -> None:
    # 循序渐进：每档关键词数 / worker / 档间冷却
    plan: List[Tuple[int, int, str, int]] = [
        # keywords, workers, source, cooldown_after
        (2, 1, "mercari", 20),
        (4, 2, "mercari", 30),
        (6, 3, "mercari", 40),
        (8, 5, "mercari", 45),
        (4, 2, "paypay", 25),
        (6, 3, "paypay", 30),
    ]

    all_kws = _load_keywords(20)
    if not all_kws:
        print("旧 subscriptions.json 无关键词，退出")
        return

    print(f"可用旧关键词 {len(all_kws)} 个，开始循序压测（不推送）…")
    report: Dict[str, Any] = {
        "started_at": int(time.time()),
        "waves": [],
        "recommendation": "",
    }

    for i, (n_kw, workers, source, cool) in enumerate(plan, 1):
        kws = all_kws[:n_kw]
        print(
            f"\n=== 第{i}档 source={source} keywords={len(kws)} workers={workers} ==="
        )
        wave = _run_wave(kws, source=source, workers=workers)
        report["waves"].append(wave)
        print(
            f"结果: ok={wave['ok']}/{wave['keyword_count']} fail={wave['fail']} "
            f"risk_like={wave['risk_like']} 耗时={wave['elapsed_seconds']}s "
            f"均={wave['avg_item_seconds']}s/词"
        )
        if wave["failures"]:
            for f in wave["failures"][:3]:
                print(f"  FAIL {f['keyword'][:40]} :: {f['error'][:120]}")

        # 若本档 risk 过高，停止加码
        if wave["keyword_count"] and wave["risk_like"] / wave["keyword_count"] >= 0.5:
            print("本档疑似风控比例偏高，停止继续加压。")
            break
        if i < len(plan) and cool > 0:
            print(f"冷却 {cool}s …")
            time.sleep(cool)

    # 给建议
    safe_workers = 1
    for w in report["waves"]:
        if w["fail"] == 0 and w["source"] == "mercari":
            safe_workers = max(safe_workers, int(w["workers"]))
        elif w["source"] == "mercari" and w["risk_like"] == 0 and w["fail"] <= 1:
            safe_workers = max(safe_workers, int(w["workers"]))
    report["recommendation"] = (
        f"建议 search_pool.max_workers 先用 {safe_workers}；"
        f"若 fail/risk 仍偶发，保持 serial_below_or_equal>=任务数 或降低 per_worker_capacity。"
    )
    print("\n" + report["recommendation"])

    out = os.path.join(ROOT, "data", "probe_concurrency.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
