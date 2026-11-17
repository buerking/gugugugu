"""
直接测试双用户：搜索 -> DeepSeek -> 保存报告 -> 尝试邮件推送。

用法（在 big 目录下）:
  python scripts/run_test.py
  python scripts/run_test.py --no-ai          # 只测搜索
  python scripts/run_test.py --keywords-limit 2
  python scripts/run_test.py --user GPU01
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import settings  # noqa: E402
from src.pipeline import processor  # noqa: E402


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("run_test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="", help="只跑指定 sub_user_id，如 GPU01 / KEY01")
    parser.add_argument("--keywords-limit", type=int, default=0, help="每用户只测前 N 个关键词，0=全部")
    parser.add_argument("--ai-limit", type=int, default=30, help="每用户送入 AI 的最大商品数")
    parser.add_argument("--no-ai", action="store_true", help="关闭 AI，仅搜索并写报告占位")
    parser.add_argument("--no-notify", action="store_true", help="不发邮件/企微，只出报告")
    args = parser.parse_args()

    if args.no_ai:
        settings.DEEPSEEK["enabled"] = False

    subs = processor.load_subscriptions()
    if args.user:
        subs = [s for s in subs if str(s.get("sub_user_id")) == args.user]
    if not subs:
        logger.error("没有可测试的订阅")
        return

    # 临时裁剪关键词，加速首测
    if args.keywords_limit > 0:
        for s in subs:
            kws = processor._keywords_of(s)
            s["keywords"] = kws[: args.keywords_limit]

    if args.no_notify:
        for s in subs:
            s["notify_channels"] = []

    # 邮件未配齐时仍跑通搜索+AI，推送失败只记日志
    email_cfg = settings.EMAIL or {}
    if not (email_cfg.get("smtp_user") and email_cfg.get("smtp_password")):
        logger.warning("SMTP 未配置完整：将只保存报告，邮件推送会失败/跳过")
        for s in subs:
            # 保留 email 通道尝试；dispatcher 会捕获错误
            pass

    logger.info("开始测试，用户数=%s", len(subs))
    seen_map: dict = {}
    total = 0
    for sub in subs:
        total += processor.process_subscription(
            sub,
            seen_map=seen_map,
            first_run=False,
            force_notify=True,
            ai_limit=args.ai_limit,
        )
    logger.info("测试结束，推送成功件数合计=%s（详见 data/reports）", total)


if __name__ == "__main__":
    main()
