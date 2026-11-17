"""SMTP 邮件推送：简洁多链接正文。"""

from __future__ import annotations

import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List

from src import settings
from src.notify.present import display_price, display_title, fx_footer, original_title


logger = logging.getLogger(__name__)


def _clip(text: str, n: int = 10) -> str:
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n]


def build_subject(items: List[Dict[str, Any]]) -> str:
    parts = []
    for it in items[:6]:
        parts.append(f"{_clip(display_title(it), 8)} {display_price(it)}")
    body = "，".join(parts)
    return f"【{len(items)}件上新】{body}"


def build_body(items: List[Dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {display_title(it)}")
        orig = original_title(it)
        if orig:
            lines.append(f"   原标题：{orig}")
        lines.append(f"   价格：{display_price(it)}")
        lines.append(f"   链接：{it.get('item_url')}")
        lines.append("")
    footer = fx_footer(items)
    if footer:
        lines.append(footer)
    return "\n".join(lines).strip()


def send_items(subscription: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    cfg = settings.EMAIL or {}
    if not cfg.get("enabled", True):
        logger.info("邮件推送已关闭")
        return

    to_addr = str(subscription.get("user_email") or cfg.get("default_to") or "").strip()
    if not to_addr:
        raise RuntimeError("订阅未配置 user_email")

    host = str(cfg.get("smtp_host") or "").strip()
    port = int(cfg.get("smtp_port") or 465)
    user = str(cfg.get("smtp_user") or "").strip()
    password = str(cfg.get("smtp_password") or "").strip()
    from_addr = str(cfg.get("from_addr") or user).strip()
    from_name = str(cfg.get("from_name") or "").strip()
    use_ssl = bool(cfg.get("use_ssl", True))

    if not host or not user or not password:
        raise RuntimeError("email.smtp_host / smtp_user / smtp_password 未配置完整")

    subject = build_subject(items)
    body = build_body(items)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), from_addr)) if from_name else from_addr
    msg["To"] = to_addr

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if cfg.get("use_starttls", True):
                smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())

    logger.info("邮件已发送 -> %s 共 %s 件 subject=%s", to_addr, len(items), subject[:80])


def send_plain(subscription: Dict[str, Any], subject: str, body: str) -> None:
    """发送任意主题/正文的纯文本邮件（物流提醒等）。"""
    cfg = settings.EMAIL or {}
    if not cfg.get("enabled", True):
        logger.info("邮件推送已关闭")
        return

    to_addr = str(subscription.get("user_email") or cfg.get("default_to") or "").strip()
    if not to_addr:
        raise RuntimeError("订阅未配置 user_email")

    host = str(cfg.get("smtp_host") or "").strip()
    port = int(cfg.get("smtp_port") or 465)
    user = str(cfg.get("smtp_user") or "").strip()
    password = str(cfg.get("smtp_password") or "").strip()
    from_addr = str(cfg.get("from_addr") or user).strip()
    from_name = str(cfg.get("from_name") or "").strip()
    use_ssl = bool(cfg.get("use_ssl", True))

    if not host or not user or not password:
        raise RuntimeError("email.smtp_host / smtp_user / smtp_password 未配置完整")

    msg = MIMEText(body or "", "plain", "utf-8")
    msg["Subject"] = Header(subject or "通知", "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), from_addr)) if from_name else from_addr
    msg["To"] = to_addr

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if cfg.get("use_starttls", True):
                smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())

    logger.info("纯文本邮件已发送 -> %s subject=%s", to_addr, (subject or "")[:80])
