# -*- coding: utf-8 -*-
"""查看上一轮公共/VIP 搜索落库样本，并现场再打一次公共两条接口。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def dump_seen() -> None:
    path = os.path.join(ROOT, "data", "seen_items.sqlite3")
    con = sqlite3.connect(path)
    print("=== seen 按订阅统计（title 样本）===")
    rows = con.execute(
        """
        SELECT sub_user_id, COUNT(*),
               SUM(CASE WHEN pushed=1 THEN 1 ELSE 0 END)
        FROM seen_items
        GROUP BY sub_user_id
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()
    for sid, n, pushed in rows:
        print(f"  scope={sid}  seen={n}  pushed={pushed}")

    for sid in ("7", "8"):
        print(f"\n--- 公共 subscriptionId={sid} 明细 ---")
        items = con.execute(
            """
            SELECT item_key, source_site, item_id, keyword, title, price, first_seen, pushed
            FROM seen_items WHERE sub_user_id=? ORDER BY first_seen DESC LIMIT 20
            """,
            (sid,),
        ).fetchall()
        if not items:
            print("  (无记录：上轮 raw=0，基线也没写入商品)")
        for it in items:
            print(it)

    print("\n--- VIP 各 scope 前 3 条标题 ---")
    vip_scopes = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT sub_user_id FROM seen_items WHERE sub_user_id LIKE 'vip:%' ORDER BY 1"
        )
    ]
    for sid in vip_scopes:
        print(f"\n[{sid}]")
        items = con.execute(
            """
            SELECT source_site, item_id, title, price
            FROM seen_items WHERE sub_user_id=? ORDER BY first_seen DESC LIMIT 3
            """,
            (sid,),
        ).fetchall()
        for it in items:
            print(" ", it)
    con.close()


def live_public() -> None:
    print("\n=== 现场再搜公共两条（看接口现在返回什么）===")
    from src.pipeline import jpkix_api
    from src.subscriptions_index import jobs_from_backend_rows
    from src.pipeline.search_engine import _run_keyword_source_task, _run_seller_task

    rows = jpkix_api.fetch_all_subscriptions()
    jobs = jobs_from_backend_rows(rows)
    created_after = str(int(time.time()) - 24 * 3600)
    for job in jobs:
        print(
            f"\njob sid={job.subscription_id} type={job.job_type} "
            f"web={job.web_id} target={job.keyword or job.seller_id}"
        )
        if job.job_type == "seller":
            key, src, items, err = _run_seller_task(job, created_after)
        else:
            src = (job.sources or ["mercari"])[0]
            key, src, items, err = _run_keyword_source_task(job, src, created_after)
        print(f"  err={err!r} count={len(items)}")
        for it in (items or [])[:5]:
            print(
                " ",
                {
                    "id": it.get("id"),
                    "name": (it.get("name") or "")[:80],
                    "price": it.get("price"),
                    "source_site": it.get("source_site"),
                    "item_url": it.get("item_url"),
                    "created": it.get("created") or it.get("created_time"),
                },
            )


if __name__ == "__main__":
    out_path = os.path.join(ROOT, "data", "_inspect_last_round.txt")
    class _Tee:
        def __init__(self, path: str) -> None:
            self.f = open(path, "w", encoding="utf-8")
        def write(self, s: str) -> int:
            self.f.write(s)
            try:
                sys.__stdout__.buffer.write(s.encode("utf-8", errors="replace"))
            except Exception:
                pass
            return len(s)
        def flush(self) -> None:
            self.f.flush()
    sys.stdout = _Tee(out_path)  # type: ignore
    try:
        dump_seen()
        live_public()
    finally:
        sys.stdout.f.close()  # type: ignore
        sys.stdout = sys.__stdout__
        print("wrote", out_path)
