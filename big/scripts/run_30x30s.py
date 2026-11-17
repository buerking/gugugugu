# -*- coding: utf-8 -*-
"""连续跑 30 轮，每轮间隔 30 秒，然后退出。"""
from __future__ import annotations

import logging
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import settings  # noqa: E402
from src.pipeline.processor import run_once  # noqa: E402

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("run_30x30s")

ROUNDS = 30
INTERVAL_SEC = 30


def main() -> None:
    logger.info("开始连续运行：共 %s 轮，间隔 %s 秒", ROUNDS, INTERVAL_SEC)
    for i in range(1, ROUNDS + 1):
        logger.info("======== 第 %s/%s 轮 ========", i, ROUNDS)
        try:
            run_once(first_run=False, force_notify=False)
        except Exception:
            logger.exception("第 %s 轮失败，继续下一轮", i)
        if i < ROUNDS:
            logger.info("等待 %s 秒…", INTERVAL_SEC)
            time.sleep(INTERVAL_SEC)
    logger.info("30 轮全部结束，已停止。")


if __name__ == "__main__":
    main()
