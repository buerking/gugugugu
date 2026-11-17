"""审计当前 track_ids：拉轨迹 + 对照旧全文匹配 / 新仅最新匹配，人工查漏。"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from src.logistics.monitor import (  # noqa: E402
    DEFAULT_KEYWORDS,
    _logistics_cfg,
    _status_record,
    fetch_statuses,
    get_saved_track_ids,
    match_keywords,
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    ids = get_saved_track_ids()
    cfg = _logistics_cfg()
    kws = list(cfg.get("keywords") or DEFAULT_KEYWORDS)

    print("=== 扫描范围 ===")
    print("track_ids 数量:", len(ids))
    print("关键词:", kws)
    print("单号列表:")
    for i, tid in enumerate(ids, 1):
        print(f"  {i:2d}. {tid}")

    print("\n=== 正在查询 17TRACK/回退 ===")
    statuses = fetch_statuses(ids)
    print("有返回轨迹:", len(statuses), "/", len(ids))
    missing = []
    for tid in ids:
        rec = _status_record(statuses.get(tid))
        if not (rec.get("detail") or "").strip():
            missing.append(tid)
    if missing:
        print("无轨迹:", missing)

    hits = match_keywords(statuses, kws)
    hit_set = {h["track_id"] for h in hits}

    rows = []
    false_old = []
    for tid in ids:
        rec = _status_record(statuses.get(tid))
        detail = rec.get("detail") or ""
        latest = rec.get("latest") or ""
        old_kw = None
        for kw in kws:
            if kw in detail:
                old_kw = kw
                break
        new_kw = None
        for kw in kws:
            if latest and kw in latest:
                new_kw = kw
                break
        status_line = ""
        for line in detail.splitlines():
            if line.startswith("状态："):
                status_line = line
                break
        row = {
            "tid": tid,
            "latest": latest,
            "status": status_line,
            "old_kw": old_kw,
            "new_kw": new_kw,
            "in_hits": tid in hit_set,
            "has_detail": bool(detail.strip()),
        }
        rows.append(row)
        if old_kw and not new_kw:
            false_old.append(row)

    print("\n=== 逐单对照（旧=全文扫 / 新=仅最新）===")
    for r in rows:
        flags = []
        if r["old_kw"] and not r["new_kw"]:
            flags.append("旧误报已修正")
        if r["new_kw"]:
            flags.append("新命中:" + r["new_kw"])
        if not r["has_detail"]:
            flags.append("无轨迹")
        flag_s = " / ".join(flags) if flags else "都不命中"
        print(
            f"{r['tid']} | 新={r['new_kw'] or '-'} | 旧全文={r['old_kw'] or '-'} | {flag_s}"
        )
        print(f"  最新: {r['latest'] or '(空)'}")
        if r["status"]:
            print(f"  {r['status']}")

    print("\n=== 汇总 ===")
    print("新逻辑命中数:", len(hits))
    for h in hits:
        print(f"  {h['track_id']} -> {h['keyword']} | { (h.get('latest') or '')[:100] }")
    print("旧逻辑会误报(全文有词但最新无):", len(false_old))
    for r in false_old:
        print(f"  {r['tid']} 旧={r['old_kw']} | 最新={ (r['latest'] or '')[:100] }")

    suspect_tokens = [
        "缴税",
        "申报",
        "派送",
        "您的邮件已",
        "保管",
        "不在",
        "投递失败",
        "待取",
        "清关",
        "税",
    ]
    print("\n=== 可疑漏匹配（最新含相关字但未进 hits）===")
    n_sus = 0
    for r in rows:
        if r["in_hits"] or not r["latest"]:
            continue
        toks = [t for t in suspect_tokens if t in r["latest"]]
        if toks:
            n_sus += 1
            print(f"  {r['tid']} tokens={toks} | {r['latest'][:140]}")
    if n_sus == 0:
        print("  无")

    # 另：最新未命中但状态码像派送/待取
    print("\n=== 状态码提示（最新无中文关键词时）===")
    for r in rows:
        if r["in_hits"] or not r["status"]:
            continue
        st = r["status"]
        if any(x in st for x in ("OutForDelivery", "AvailableForPickup", "DeliveryFailure", "Expired")):
            print(f"  {r['tid']} {st} | 最新={ (r['latest'] or '')[:100] }")

    out = Path(APP_ROOT) / "data" / "logistics_poll_audit.json"
    out.write_text(
        json.dumps(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "ids": ids,
                "keywords": kws,
                "hit_count": len(hits),
                "hits": [
                    {
                        "track_id": h["track_id"],
                        "keyword": h["keyword"],
                        "latest": h.get("latest"),
                    }
                    for h in hits
                ],
                "false_old_count": len(false_old),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n已写入", out)


if __name__ == "__main__":
    main()
