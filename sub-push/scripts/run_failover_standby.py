"""开发端灾备监控入口：python scripts/run_failover_standby.py"""

from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src import settings  # noqa: E402
from src.failover.standby import run_standby  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    run_standby(log=logging.getLogger("failover-standby"))


if __name__ == "__main__":
    main()
