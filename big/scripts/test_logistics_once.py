"""物流监控单次测试：Edge 抓单号 → 17track → 命中发邮件给显卡用户。"""

from __future__ import annotations

import json
import logging
import os
import sys


APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from src import settings  # noqa: E402
from src.logistics.monitor import run_logistics_once  # noqa: E402


def _load_gpu_sub() -> dict:
    with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        subs = json.load(f)
    for s in subs:
        if s.get("logistics_monitor") or s.get("sub_user_id") == "GPU01":
            return s
    raise RuntimeError("未找到显卡用户订阅 GPU01")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sub = _load_gpu_sub()
    print(f"订阅用户: {sub.get('customer_name')} <{sub.get('user_email')}>")
    print("使用本机 Edge(msedge) 抓后台单号（不碰 Chrome）；轨迹走官网 HTTP，不用 17track…")

    # 测试：全部分页；命中后最多发 5 封，避免「派送」刷屏
    result = run_logistics_once(
        sub,
        skip_seen=True,  # 刚才 HTTP 测过的缴税单会跳过，发其它命中
        keep_browser_open=False,
        max_send=5,
    )
    tracks = result.get("tracks") or []
    hits = result.get("hits") or []
    sent = result.get("sent") or []
    print(f"抓取单号数: {len(tracks)}")
    print("单号:", ", ".join(tracks))
    print(f"关键词命中: {len(hits)}")
    for h in hits:
        print(f"  - {h['track_id']} | {h['keyword']} | detail_len={len(h.get('detail') or '')}")
    print(f"已发邮件: {len(sent)}")
    for h in sent:
        print(f"  -> {h['track_id']} {h['keyword']}")
    if not hits:
        print("本轮无关键词命中（仍完成抓取与 17track 查询）。")


if __name__ == "__main__":
    main()
