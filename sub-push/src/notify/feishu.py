"""飞书自定义机器人 Webhook 通知。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

from src import settings


logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_sent: Dict[str, float] = {}


def _cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "FEISHU", None) or {})


def send_text(text: str, *, tag: str = "default") -> bool:
    """
    发送文本。同 tag 在 cooldown 内不重复刷屏。
    """
    cfg = _cfg()
    if not cfg.get("enabled", True):
        logger.info("飞书通知未启用，跳过: %s", text[:120])
        return False
    url = str(cfg.get("webhook_url") or "").strip()
    if not url:
        logger.warning("飞书 webhook_url 未配置")
        return False

    cooldown = float(cfg.get("cooldown_seconds") or 300)
    now = time.time()
    with _lock:
        last = _last_sent.get(tag) or 0
        if cooldown > 0 and now - last < cooldown:
            logger.info("飞书通知冷却中 tag=%s skip", tag)
            return False
        _last_sent[tag] = now

    title = str(cfg.get("title_prefix") or "[sub-push]")
    body = f"{title}\n{text}"
    payload = {"msg_type": "text", "content": {"text": body[:3500]}}
    timeout = float(cfg.get("timeout_seconds") or 10)
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        ok = 200 <= resp.status_code < 300
        logger.info("飞书通知 status=%s tag=%s body=%s", resp.status_code, tag, (resp.text or "")[:200])
        return ok
    except Exception as exc:
        logger.warning("飞书通知失败: %s", exc)
        return False


def notify_failure(summary: str, *, detail: str = "", tag: str = "failure") -> None:
    lines = [f"⚠️ 故障告警", summary]
    if detail:
        lines.append(detail[:1500])
    send_text("\n".join(lines), tag=tag)


def notify_heartbeat(summary: str) -> None:
    send_text(f"💓 心跳正常\n{summary}", tag="heartbeat")


def notify_recovered(summary: str) -> None:
    send_text(f"✅ 已恢复\n{summary}", tag="recovered")
