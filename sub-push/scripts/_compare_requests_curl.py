# -*- coding: utf-8 -*-
"""手动对比 requests vs curl：公共卖家/关键词 + 抽样 VIP。"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = os.path.join(ROOT, "data", "_compare_requests_curl.txt")


def log(f, *args):
    line = " ".join(str(a) for a in args)
    f.write(line + "\n")
    f.flush()


def main() -> None:
    from src.pipeline import jpkix_api
    from src.subscriptions_index import jobs_from_backend_rows
    from src.sources import mercari_web, paypay_web
    from src.sources.mercari_seller_client import search_seller_new_listings
    from src.pipeline.search_engine import (
        _created_after_for_job,
        _filter_by_created_after,
        _run_keyword_source_task,
        _run_seller_task,
    )
    from src.pipeline.seen_db import SeenDB
    from src import settings

    with open(OUT, "w", encoding="utf-8") as f:
        log(f, "=== compare @", datetime.now().isoformat(), "===")

        # 1) SSL 裸测
        import requests

        for host in (
            "https://api.mercari.jp/",
            "https://paypayfleamarket.yahoo.co.jp/",
            "https://auctions.yahoo.co.jp/",
        ):
            t0 = time.time()
            try:
                r = requests.get(host, timeout=10, verify=True)
                log(f, f"requests GET {host} -> {r.status_code} {time.time()-t0:.2f}s")
            except Exception as e:
                log(f, f"requests GET {host} FAIL {time.time()-t0:.2f}s :: {e}")

        # 2) 拉线上订阅
        rows = jpkix_api.fetch_all_subscriptions()
        jobs = jobs_from_backend_rows(rows)
        log(f, "\nbackend jobs:", len(jobs))
        for j in jobs:
            log(
                f,
                f"  sid={j.subscription_id} type={j.job_type} webId={j.backend_web_id} "
                f"startTime={j.created_time} target={j.keyword or j.seller_id}",
            )
            try:
                st = int(float(j.created_time or 0))
                log(f, f"    startTime iso={datetime.fromtimestamp(st).isoformat()}")
            except Exception:
                pass

        seen = SeenDB(settings.SEEN_DB_PATH)
        now = int(time.time())
        log(f, f"\nnow={now} iso={datetime.fromtimestamp(now).isoformat()}")

        # 3) 时间窗计算
        for j in jobs:
            ca = _created_after_for_job(
                j, first_run=False, seen=seen, interval_minutes=5
            )
            ca0 = _created_after_for_job(
                j, first_run=True, seen=seen, interval_minutes=5
            )
            log(
                f,
                f"sid={j.subscription_id} created_after first_run={ca0} "
                f"({datetime.fromtimestamp(int(ca0)).isoformat()}) "
                f"normal={ca} ({datetime.fromtimestamp(int(ca)).isoformat()})",
            )

        # 4) 卖家：未过滤 vs 过滤
        for j in jobs:
            if j.job_type != "seller":
                continue
            log(f, f"\n--- SELLER raw API seller_id={j.seller_id} ---")
            try:
                # 不过滤时间：传很旧的阈值
                raw = search_seller_new_listings(
                    seller_id=j.seller_id,
                    created_after_date="0",
                    limit=100,
                    as_shop=bool(j.mercari_as_shop),
                )
                log(f, f"raw count (created_after=0 filter inside client)={len(raw)}")
                for it in raw[:5]:
                    log(
                        f,
                        " ",
                        {
                            "id": it.get("id"),
                            "name": (it.get("name") or "")[:60],
                            "price": it.get("price"),
                            "created": it.get("created"),
                            "status": it.get("status"),
                        },
                    )
            except Exception:
                log(f, traceback.format_exc())

            ca = _created_after_for_job(
                j, first_run=False, seen=seen, interval_minutes=5
            )
            key, src, items, err = _run_seller_task(j, ca)
            log(f, f"pipeline seller with time window ca={ca}: count={len(items)} err={err}")

            # 再用 now-7d
            ca7 = str(now - 7 * 86400)
            key, src, items7, err7 = _run_seller_task(j, ca7)
            log(f, f"pipeline seller ca=7d: count={len(items7)} err={err7}")
            for it in (items7 or [])[:5]:
                log(
                    f,
                    " ",
                    {
                        "id": it.get("id"),
                        "name": (it.get("name") or "")[:60],
                        "price": it.get("price"),
                        "created": it.get("created"),
                    },
                )

        # 5) 关键词：未过滤时间窗 vs 过滤
        for j in jobs:
            if j.job_type != "keyword":
                continue
            log(f, f"\n--- KEYWORD raw API kw={j.keyword!r} ---")
            try:
                raw = mercari_web.search(
                    {
                        "keyword": j.keyword,
                        "price_min": j.price_min,
                        "price_max": j.price_max,
                    }
                )
                log(f, f"mercari_web.search raw count={len(raw)}")
                for it in raw[:5]:
                    log(
                        f,
                        " ",
                        {
                            "id": it.get("id"),
                            "name": (it.get("name") or "")[:60],
                            "price": it.get("price"),
                            "created": it.get("created"),
                        },
                    )
                ca = _created_after_for_job(
                    j, first_run=False, seen=seen, interval_minutes=5
                )
                filtered = _filter_by_created_after(raw, ca)
                log(f, f"after time filter ca={ca}: {len(filtered)}")
                filtered7 = _filter_by_created_after(raw, str(now - 7 * 86400))
                log(f, f"after 7d filter: {len(filtered7)}")
            except Exception:
                log(f, traceback.format_exc())

            key, src, items, err = _run_keyword_source_task(j, "mercari", str(now - 7 * 86400))
            log(f, f"pipeline keyword 7d: count={len(items)} err={err}")

        # 6) VIP 抽样 Keyence / quadro
        log(f, "\n--- VIP sample paypay quadro p4000 ---")
        try:
            raw = paypay_web.search({"keyword": "quadro p4000"})
            log(f, f"paypay raw={len(raw)}")
            for it in raw[:3]:
                log(f, " ", it.get("id"), (it.get("name") or "")[:50], it.get("price"))
        except Exception:
            log(f, traceback.format_exc())

        log(f, "\nDONE wrote", OUT)


if __name__ == "__main__":
    main()
    print("wrote", OUT)
