"""
企业微信推送方案（专供版）：

1) external_contact — 客户联系群发，发给「企业微信添加的微信客户」
   API: /cgi-bin/externalcontact/add_msg_template
   需要：corp_id + corp_secret（客户联系权限）+ external_userid

2) app — 应用消息，发给企业内成员 userid
   API: /cgi-bin/message/send

3) webhook — 群机器人 webhook（调试/单人临时通道最简单）
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from src import settings
from src.notify.present import display_price, display_title, fx_footer, original_title


logger = logging.getLogger(__name__)

_token_cache: Dict[str, Any] = {"token": "", "expire_at": 0.0}


def _get_access_token() -> str:
    cfg = settings.WECOM or {}
    corp_id = str(cfg.get("corp_id") or "").strip()
    secret = str(cfg.get("corp_secret") or "").strip()
    if not corp_id or not secret:
        raise RuntimeError("wecom.corp_id / corp_secret 未配置")

    now = time.time()
    if _token_cache["token"] and now < float(_token_cache["expire_at"]) - 60:
        return str(_token_cache["token"])

    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    resp = requests.get(url, params={"corpid": corp_id, "corpsecret": secret}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"gettoken failed: {data}")
    token = data["access_token"]
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + int(data.get("expires_in") or 7200)
    return token


def _format_items_markdown(customer_name: str, keyword: str, items: List[Dict[str, Any]]) -> str:
    lines = [
        f"**专供推送** | 客户：{customer_name or '-'} | 关键词：{keyword}",
        f"共 {len(items)} 件通过 AI 审核：",
        "",
    ]
    for i, it in enumerate(items[:30], 1):
        site = it.get("source_site") or ""
        ltype = it.get("listing_type") or ""
        reason = it.get("ai_reason") or ""
        orig = original_title(it)
        orig_line = f"\n   原标题：{orig}" if orig else ""
        lines.append(
            f"{i}. [{site}/{ltype}] {display_title(it)}  {display_price(it)}{orig_line}\n"
            f"   {it.get('item_url')}\n"
            f"   理由：{reason}"
        )
    if len(items) > 30:
        lines.append(f"…其余 {len(items) - 30} 件省略")
    footer = fx_footer(items)
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def _format_items_text(customer_name: str, keyword: str, items: List[Dict[str, Any]]) -> str:
    lines = [
        f"【专供推送】客户:{customer_name or '-'} 关键词:{keyword}",
        f"共{len(items)}件通过AI审核",
    ]
    for i, it in enumerate(items[:20], 1):
        lines.append(
            f"{i}.[{it.get('source_site')}/{it.get('listing_type')}] "
            f"{display_title(it)} {display_price(it)} {it.get('item_url')}"
        )
    footer = fx_footer(items)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def send_webhook(webhook_url: str, content: str) -> None:
    resp = requests.post(
        webhook_url,
        json={"msgtype": "markdown", "markdown": {"content": content}},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"webhook failed: {data}")


def send_app_message(userid: str, content: str) -> None:
    token = _get_access_token()
    agent_id = int((settings.WECOM or {}).get("agent_id") or 0)
    if not agent_id:
        raise RuntimeError("wecom.agent_id 未配置")
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": userid,
        "msgtype": "text",
        "agentid": agent_id,
        "text": {"content": content},
        "safe": 0,
    }
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"app message failed: {data}")


def send_external_contact(external_userid: str, content: str, sender_userid: str = "") -> None:
    """
    企业微信「客户联系」群发到单个微信客户。
    注意：该接口创建的是企业成员侧待发送任务，部分环境需成员在企业微信确认发送。
    文档：客户联系群发 / add_msg_template
    """
    token = _get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/externalcontact/add_msg_template?access_token={token}"
    payload: Dict[str, Any] = {
        "chat_type": "single",
        "external_userid": [external_userid],
        "text": {"content": content},
    }
    if sender_userid:
        payload["sender"] = sender_userid
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"external_contact failed: {data}")
    logger.info("企业微信客户消息已创建 msgid=%s", data.get("msgid"))


def send_customer_group(chat_id: str, content: str, sender_userid: str = "") -> None:
    """
    客户群群发（不是群机器人 webhook）。
    chat_type=group + chat_id（客户群 id）。
    客户群通常无法添加「群机器人」，请用本接口或邮件/内部群 webhook。
    """
    token = _get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/externalcontact/add_msg_template?access_token={token}"
    payload: Dict[str, Any] = {
        "chat_type": "group",
        "chat_id_list": [chat_id],
        "text": {"content": content},
    }
    if sender_userid:
        payload["sender"] = sender_userid
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"customer_group failed: {data}")
    logger.info("企业微信客户群消息已创建 msgid=%s chat=%s", data.get("msgid"), chat_id)


def push_items(subscription: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    if not items:
        return

    mode = (
        str(subscription.get("wecom_mode") or "").strip()
        or str((settings.WECOM or {}).get("default_mode") or "external_contact")
    ).lower()
    customer = str(subscription.get("customer_name") or "")
    keyword = str(subscription.get("keyword") or "")
    md = _format_items_markdown(customer, keyword, items)
    text = _format_items_text(customer, keyword, items)

    if mode == "webhook":
        webhook = str(subscription.get("webhook_url") or "").strip()
        if not webhook:
            raise RuntimeError("webhook_url 未配置（仅企业内部群可加机器人）")
        send_webhook(webhook, md)
        logger.info("已通过 webhook 推送 %s 件", len(items))
        return

    if mode == "app":
        userid = str(subscription.get("wecom_userid") or "").strip()
        if not userid:
            raise RuntimeError("wecom_userid 未配置")
        send_app_message(userid, text)
        logger.info("已通过应用消息推送 %s 件 -> %s", len(items), userid)
        return

    if mode in ("customer_group", "group"):
        chat_id = str(subscription.get("wecom_chat_id") or "").strip()
        if not chat_id:
            raise RuntimeError("wecom_chat_id（客户群 id）未配置")
        sender = str(subscription.get("wecom_userid") or "").strip()
        send_customer_group(chat_id, text, sender_userid=sender)
        logger.info("已通过客户群推送 %s 件 -> %s", len(items), chat_id)
        return

    # default: external_contact
    external_userid = str(subscription.get("wecom_external_userid") or "").strip()
    if not external_userid:
        webhook = str(subscription.get("webhook_url") or "").strip()
        if webhook:
            send_webhook(webhook, md)
            logger.info("external_userid 空，已回退 webhook 推送 %s 件", len(items))
            return
        raise RuntimeError("wecom_external_userid 未配置，且无 webhook_url")
    sender = str(subscription.get("wecom_userid") or "").strip()
    send_external_contact(external_userid, text, sender_userid=sender)
    logger.info("已通过客户联系推送 %s 件 -> %s", len(items), external_userid)
