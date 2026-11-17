"""不启浏览器：测 17TRACK → 追跡なう回退 → 关键词 → 可选发信。"""

from __future__ import annotations

import json
import logging
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

# 强制重载 settings（config 刚改过）
import importlib
import src.settings as settings_mod

importlib.reload(settings_mod)

from src import settings  # noqa: E402
from src.logistics.monitor import fetch_statuses, match_keywords, send_logistics_emails  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = [a for a in sys.argv[1:] if a]
    send = "--send" in args
    tids = [a for a in args if a != "--send"] or ["CN135767762JP", "CN135989628JP"]
    print("查询单号:", tids)
    statuses = fetch_statuses(tids)
    for tid, rec in statuses.items():
        detail = (rec or {}).get("detail") if isinstance(rec, dict) else rec
        latest = (rec or {}).get("latest") if isinstance(rec, dict) else ""
        print(f"\n==== {tid} len={len(detail or '')} ====")
        print("最新:", (latest or "")[:200])
        print((detail or "")[:600])
    hits = match_keywords(statuses)
    print("\nhits:", [(h["track_id"], h["keyword"], (h.get("latest") or "")[:60]) for h in hits])

    if send and hits:
        with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
            sub = next(s for s in json.load(f) if s.get("sub_user_id") == "GPU01")
        sent = send_logistics_emails(sub, hits, skip_unchanged=False, max_send=2)
        print("sent:", [(h["track_id"], h["keyword"]) for h in (sent.get("sent") or sent)])


if __name__ == "__main__":
    main()
