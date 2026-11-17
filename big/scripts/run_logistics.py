"""
物流监控正式跑一轮（会发邮件）：

用法（在 big 目录下）：
  python scripts/run_logistics.py              # Edge 抓号 + 查轨迹推送
  python scripts/run_logistics.py --dry-run    # 只打印不发信、不写推送状态
  python scripts/run_logistics.py --scrape-only  # 只抓号写 JSON，关浏览器
  python scripts/run_logistics.py --poll-only    # 只用已存单号查轨迹发信
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
    _seen_path,
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


def _print_poll_result(result: dict, *, dry: bool) -> None:
    tracks = result.get("tracks") or []
    hits = result.get("hits") or []
    sent = result.get("sent") or []
    skipped = result.get("skipped") or []

    print(f"\n单号: {len(tracks)}")
    print(f"关键词命中: {len(hits)}")
    print(f"内容未变跳过: {len(skipped)}")
    for s in skipped[:20]:
        print(f"  - skip {s.get('track_id')} | {s.get('keyword')} | {s.get('reason')}")
    if len(skipped) > 20:
        print(f"  … 另有 {len(skipped) - 20} 条跳过")

    print(f"{'将发送' if dry else '已发送'}: {len(sent)}")
    for h in sent:
        print(f"  -> {h.get('track_id')} | {h.get('keyword')}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    dry = "--dry-run" in sys.argv
    scrape_only = "--scrape-only" in sys.argv
    poll_only = "--poll-only" in sys.argv
    if scrape_only and poll_only:
        print("不能同时指定 --scrape-only 与 --poll-only")
        sys.exit(2)

    sub = _load_gpu_sub()

    print("=" * 60)
    print("物流监控" + (" DRY-RUN（不发信）" if dry else "（正式）"))
    print(f"用户: {sub.get('customer_name')} <{sub.get('user_email')}>")
    print(f"状态文件: {_seen_path()}")
    print("=" * 60)

    if scrape_only:
        result = scrape_and_save_track_ids(keep_browser_open=False)
        if result.get("login_pending"):
            print(f"\n登录未通过，抓号暂缓。已有单号: {result.get('existing_count') or 0}")
            print(result.get("error") or "")
            print("\n请人工在 Edge 完成登录后再抓；可用 --poll-only 先查已有单号。")
            return
        tracks = result.get("tracks") or []
        print(f"\n已抓并写入单号: {len(tracks)}")
        for i, t in enumerate(tracks, 1):
            print(f"  {i:3d}. {t}")
        print("\n完成（仅抓号，未查轨迹）。")
        return

    if poll_only:
        print(f"使用已存单号: {len(get_saved_track_ids())}")
        result = poll_and_notify(sub, skip_unchanged=True, dry_run=dry)
        _print_poll_result(result, dry=dry)
        print("\n完成。")
        return

    result = run_logistics_once(
        sub,
        skip_unchanged=True,
        keep_browser_open=False,
        dry_run=dry,
        scrape=True,
        poll=True,
    )
    _print_poll_result(result, dry=dry)
    print("\n完成。")


if __name__ == "__main__":
    main()
