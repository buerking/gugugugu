import logging
import time

from src import settings
from src.pipeline import storage
from src.unified_cycle import UnifiedRunner, run_cycle


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_once(first_run: bool = False) -> None:
    """兼容旧入口：跑完整一轮（含冷却等待）。"""
    run_cycle(first_run=first_run)


def main() -> None:
    storage.ensure_dirs()
    runner = UnifiedRunner(log=logger)
    runner.start(run_baseline=True)
    logger.info(
        "统一守护运行中：中间层混编VIP→切片搜索；VIP AI异步队列",
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        runner.stop()
        logger.info("已停止")


if __name__ == "__main__":
    main()
