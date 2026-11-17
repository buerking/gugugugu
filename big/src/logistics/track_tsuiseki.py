"""追跡なう免费 JSON API（日本邮政等，无密钥）。"""

from __future__ import annotations

import logging
import time
from typing import Dict, Iterable, List, Optional

import requests


logger = logging.getLogger(__name__)

API = "https://tsuiseki-now.ogatomo.net/api/json/{number}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)


def latest_match_text(data: dict) -> str:
    """仅当前状态，用于关键词匹配（不含 statusHistory）。"""
    if not data or not data.get("isSuccess"):
        return ""
    return f"{data.get('status') or ''} {data.get('updateAt') or ''}".strip()


def flatten_detail(data: dict) -> str:
    if not data or not data.get("isSuccess"):
        return ""
    lines: List[str] = [
        f"单号：{data.get('trackingNumber') or ''}",
        f"承运：{data.get('type') or ''}",
        f"品名：{data.get('productName') or ''}",
        f"当前：{data.get('status') or ''} {data.get('updateAt') or ''}",
    ]
    for h in data.get("statusHistory") or []:
        lines.append(
            f"{h.get('updateAt') or ''} {h.get('status') or ''} {h.get('officeName') or ''}".strip()
        )
    return "\n".join(x for x in lines if x and x.strip(" ：")).strip()


def fetch_one_record(
    track_id: str, *, carrier: str = "post", timeout: float = 25.0
) -> Dict[str, str]:
    tid = track_id.strip().upper()
    url = API.format(number=tid)
    r = requests.get(
        url,
        params={"carrier": carrier} if carrier else None,
        timeout=timeout,
        headers={"User-Agent": UA},
    )
    r.raise_for_status()
    data = r.json()
    return {
        "detail": flatten_detail(data),
        "latest": latest_match_text(data),
    }


def fetch_one(track_id: str, *, carrier: str = "post", timeout: float = 25.0) -> str:
    return fetch_one_record(track_id, carrier=carrier, timeout=timeout).get("detail") or ""


def fetch_many(
    track_ids: Iterable[str],
    *,
    carrier: str = "post",
    delay_seconds: float = 0.4,
    timeout: float = 25.0,
) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    ids = [str(t).strip().upper() for t in track_ids if str(t).strip()]
    for i, tid in enumerate(ids, 1):
        try:
            rec = fetch_one_record(tid, carrier=carrier, timeout=timeout)
            out[tid] = rec
            logger.info(
                "tsuiseki %s/%s %s len=%s",
                i,
                len(ids),
                tid,
                len(rec.get("detail") or ""),
            )
        except Exception as e:
            logger.warning("tsuiseki 失败 %s: %s", tid, e)
            out[tid] = {"detail": "", "latest": ""}
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return out
