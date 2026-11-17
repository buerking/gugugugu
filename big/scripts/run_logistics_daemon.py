"""
物流双节奏常驻进程（命令行）。

用法（在 big 目录下）：
  python scripts/run_logistics_daemon.py
  python scripts/run_logistics_daemon.py --scrape-now
  python scripts/run_logistics_daemon.py --poll-now

也可直接用 GUI：python app.py 或「启动big专供推送.bat」
"""

from __future__ import annotations

import logging
import os
import sys
import time


APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from src.logistics.daemon import LogisticsDaemon  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scrape_now = "--scrape-now" in sys.argv
    poll_now = "--poll-now" in sys.argv
    daemon = LogisticsDaemon()
    daemon.start(scrape_now=scrape_now, poll_now=poll_now)
    try:
        while daemon.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        daemon.stop()
        while daemon.running:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
