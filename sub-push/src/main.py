import logging
import time

from src import settings
from src.logistics_sidecar import LogisticsSidecar
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
    logistics = LogisticsSidecar(log=logger)
    runner.start(run_baseline=True)
    logistics.start(poll_now=True, scrape_now=False)
    logger.info(
        "统一守护运行中：普通订阅+大客户VIP混编搜索；物流旁路进程；VIP AI异步队列",
    )
    try:
        while True:
            time.sleep(1)
            if not logistics.running and logistics.last_error:
                # 仅提示一次后清空，避免刷屏；守护本身不自动重启物流
                logger.warning("物流旁路已退出: %s", logistics.last_error)
                logistics.last_error = ""
    except KeyboardInterrupt:
        runner.stop()
        logistics.stop()
        logger.info("已停止")


if __name__ == "__main__":
    main()
