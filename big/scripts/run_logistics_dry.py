"""物流监控干跑：只打印，不发邮件（查轨迹时也不写推送状态）。

用法：
  python scripts/run_logistics_dry.py
  python scripts/run_logistics_dry.py --scrape-only
  python scripts/run_logistics_dry.py --poll-only
"""

from __future__ import annotations

import json
import logging
import os
import sys


APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from src import settings  # noqa: E402
from src.logistics.monitor import (  # noqa: E402
    get_saved_track_ids,
    poll_and_notify,
    run_logistics_once,
    scrape_and_save_track_ids,
)


def _load_gpu_sub() -> dict:
    with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        subs = json.load(f)
    for s in subs:
        if s.get("logistics_monitor") or s.get("sub_user_id") == "GPU01":
            return s
    raise RuntimeError("未找到显卡用户订阅 GPU01")


def _preview_email(hit: dict) -> None:
    tid = hit.get("track_id") or ""
    kw = hit.get("keyword") or ""
    detail = hit.get("detail") or ""
    print("-" * 60)
    print(f"【预览邮件】主题：【物流提醒】{tid} {kw}")
    print(f"收件人：将发给 GPU 用户邮箱（本脚本不发送）")
    print(f"单号：{tid}")
    print(f"匹配关键词：{kw}")
    print(f"17TRACK：https://t.17track.net/zh-cn#nums={tid}")
    print("—— 匹配信息全文 ——")
    if len(detail) > 1200:
        print(detail[:1200] + f"\n…(共 {len(detail)} 字，已截断)")
    else:
        print(detail)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scrape_only = "--scrape-only" in sys.argv
    poll_only = "--poll-only" in sys.argv
    if scrape_only and poll_only:
        print("不能同时指定 --scrape-only 与 --poll-only")
        sys.exit(2)

    sub = _load_gpu_sub()
    print("=" * 60)
    print("物流监控 DRY-RUN")
    print(f"订阅用户: {sub.get('customer_name')} <{sub.get('user_email')}>")
    print("=" * 60)

    if scrape_only:
        # 抓号需要持久化 track_ids，否则守护进程/poll-only 无数据
        result = scrape_and_save_track_ids(keep_browser_open=False)
        tracks = result.get("tracks") or []
        print(f"\n已抓并写入单号: {len(tracks)}")
        for i, t in enumerate(tracks, 1):
            print(f"  {i:3d}. {t}")
        print("\n（仅抓号；未查轨迹、未发邮件）")
        return

    if poll_only:
        print(f"使用已存单号: {len(get_saved_track_ids())}")
        result = poll_and_notify(sub, skip_unchanged=True, dry_run=True)
    else:
        result = run_logistics_once(
            sub,
            skip_unchanged=True,
            keep_browser_open=False,
            dry_run=True,
            scrape=True,
            poll=True,
        )

    tracks = result.get("tracks") or []
    hits = result.get("hits") or []
    sent = result.get("sent") or []
    skipped = result.get("skipped") or []
    statuses = result.get("statuses") or {}

    print(f"\n单号数: {len(tracks)}")
    for i, t in enumerate(tracks, 1):
        rec = statuses.get(t)
        if isinstance(rec, dict):
            has = "有轨迹" if (rec.get("detail") or "").strip() else "无轨迹"
        else:
            has = "有轨迹" if (rec or "").strip() else "无轨迹"
        print(f"  {i:3d}. {t}  [{has}]")

    print(f"\n关键词命中: {len(hits)}")
    print(f"内容未变将跳过: {len(skipped)}")
    print(f"内容有变化将推送: {len(sent)}")
    if not sent:
        print("（无需要推送的项）")
    else:
        for h in sent:
            _preview_email(h)

    print("\n" + "=" * 60)
    print("DRY-RUN 结束：未发送邮件；查轨迹未写入推送 hash。")
    print("=" * 60)


if __name__ == "__main__":
    main()
