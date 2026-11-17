import logging
import time

from src import settings
from src.pipeline.processor import run_once


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    interval = max(1, int(settings.SCHEDULE_INTERVAL_SECONDS))
    logger.info("big 专供版启动，间隔=%s 秒", interval)

    # 首次先建基线，避免把全站存量刷给客户
    try:
        run_once(first_run=True)
    except Exception as exc:
        logger.exception("首轮失败: %s", exc)

    # 本轮跑完再等 interval，避免搜索耗时 > 间隔时叠跑
    try:
        while True:
            try:
                run_once(first_run=False)
            except Exception as e:
                logger.exception("执行失败: %s", e)
            logger.info("等待 %s 秒后下一轮…", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("已停止")


if __name__ == "__main__":
    main()
