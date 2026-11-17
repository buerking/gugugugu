"""统一通知：默认邮件；可选企业微信。"""

import logging
from typing import Any, Dict, List

from src.notify import email_push, wecom
from src.notify.present import prepare_push_items


logger = logging.getLogger(__name__)


def push_items(subscription: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    items = prepare_push_items(items)

    channels = subscription.get("notify_channels")
    if channels is None:
        channels = ["email"]
    channels = [str(c).lower() for c in channels if str(c).strip()]
    if not channels:
        logger.info("notify_channels 为空，跳过推送（仅保留本地报告）")
        return

    errors = []

    if "email" in channels:
        try:
            email_push.send_items(subscription, items)
        except Exception as exc:
            logger.exception("邮件推送失败: %s", exc)
            errors.append(f"email: {exc}")

    if "wecom" in channels:
        try:
            wecom.push_items(subscription, items)
        except Exception as exc:
            logger.exception("企业微信推送失败: %s", exc)
            errors.append(f"wecom: {exc}")

    if errors and len(errors) == len([c for c in channels if c in ("email", "wecom")]):
        raise RuntimeError("; ".join(errors))
