"""不启浏览器：验证官网轨迹 HTTP + 关键词 + 发一封测试邮件。"""

from __future__ import annotations

import json
import logging
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from src import settings  # noqa: E402
from src.logistics.monitor import match_keywords, send_logistics_emails  # noqa: E402
from src.logistics import track_jpgoodbuy  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    tid = sys.argv[1] if len(sys.argv) > 1 else "CN135767762JP"
    with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        subs = json.load(f)
    sub = next(s for s in subs if s.get("sub_user_id") == "GPU01")

    detail = track_jpgoodbuy.fetch_track_detail(tid)
    print("url:", track_jpgoodbuy.track_page_url(tid))
    print("detail_len:", len(detail))
    print("detail:", detail[:500])
    hits = match_keywords({tid: detail})
    print("hits:", hits)
    sent = send_logistics_emails(sub, hits, skip_seen=False, max_send=1)
    print("sent:", [(h["track_id"], h["keyword"]) for h in sent])


if __name__ == "__main__":
    main()
