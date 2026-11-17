"""17TRACK 官方 Tracking API v2.4（需 17token，无人机验证）。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

import requests


logger = logging.getLogger(__name__)

BASE = "https://api.17track.net/track/v2.4"
BATCH = 40


def _headers(token: str) -> Dict[str, str]:
    return {"17token": token, "Content-Type": "application/json"}


def _post(token: str, path: str, payload: list, timeout: float = 40.0) -> Dict[str, Any]:
    url = f"{BASE}/{path.lstrip('/')}"
    r = requests.post(url, headers=_headers(token), json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"17track 响应非 JSON 对象: {path}")
    return data


def register_numbers(
    token: str,
    track_ids: Iterable[str],
    *,
    carrier: Optional[int] = None,
) -> Dict[str, Any]:
    """注册单号（不要带 lang，部分账号会报 language not support）。已注册可忽略。"""
    ids = [str(t).strip().upper() for t in track_ids if str(t).strip()]
    summary: Dict[str, Any] = {"accepted": [], "rejected": []}
    for i in range(0, len(ids), BATCH):
        batch = ids[i : i + BATCH]
        body = []
        for n in batch:
            item: Dict[str, Any] = {"number": n}
            if carrier is not None:
                item["carrier"] = carrier
            body.append(item)
        resp = _post(token, "register", body)
        data = resp.get("data") or {}
        accepted = data.get("accepted") or []
        rejected = data.get("rejected") or []
        # -18019901 已注册视为成功
        real_rej = []
        for rej in rejected:
            err = rej.get("error") or {}
            if err.get("code") == -18019901:
                accepted.append({"number": rej.get("number"), "carrier": rej.get("carrier")})
            else:
                real_rej.append(rej)
        summary["accepted"].extend(accepted)
        summary["rejected"].extend(real_rej)
        logger.info(
            "17track register batch %s-%s code=%s accepted=%s rejected=%s",
            i + 1,
            i + len(batch),
            resp.get("code"),
            len(accepted),
            len(real_rej),
        )
    return summary


def get_track_info(
    token: str,
    track_ids: Iterable[str],
    *,
    lang: str = "zh",
) -> Dict[str, Any]:
    ids = [str(t).strip().upper() for t in track_ids if str(t).strip()]
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for i in range(0, len(ids), BATCH):
        batch = ids[i : i + BATCH]
        body = [{"number": n, "lang": lang} for n in batch]
        resp = _post(token, "gettrackinfo", body)
        data = resp.get("data") or {}
        accepted.extend(data.get("accepted") or [])
        rejected.extend(data.get("rejected") or [])
    return {"accepted": accepted, "rejected": rejected}


def _flatten_loc(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        bits = []
        for k in ("city", "state", "country", "street", "postal_code"):
            v = val.get(k)
            if v:
                bits.append(str(v))
        return " ".join(bits)
    return str(val).strip()


def _event_text(ev: Dict[str, Any]) -> str:
    parts = [
        str(ev.get("time_iso") or ev.get("time_utc") or ev.get("time_raw") or ""),
        str(ev.get("description") or ""),
        _flatten_loc(ev.get("location")),
        _flatten_loc(ev.get("address")),
    ]
    return " ".join(p for p in parts if p and p != "None").strip()


def latest_match_text(item: Dict[str, Any]) -> str:
    """
    仅取「当前最新」用于关键词匹配（不含历史事件）。
    优先 latest_event；没有则取各承运商事件列表的第一条（17TRACK 通常最新在前）。
    """
    track_info = item.get("track_info") or {}
    parts: List[str] = []

    latest = track_info.get("latest_event") or {}
    if isinstance(latest, dict) and latest:
        t = _event_text(latest)
        if t:
            parts.append(t)

    status = track_info.get("latest_status") or {}
    if isinstance(status, dict) and status:
        st = " ".join(
            str(status.get(k) or "")
            for k in ("status", "sub_status", "sub_status_descr")
            if status.get(k)
        ).strip()
        if st:
            parts.append(st)

    if not parts:
        providers = ((track_info.get("tracking") or {}).get("providers")) or []
        for prov in providers:
            events = prov.get("events") or []
            if not events:
                continue
            # 取时间最新的一条；若无时间则取列表首条
            best = None
            best_key = ""
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                key = str(
                    ev.get("time_iso") or ev.get("time_utc") or ev.get("time_raw") or ""
                )
                if best is None or (key and key > best_key):
                    best = ev
                    best_key = key
            if best is None and isinstance(events[0], dict):
                best = events[0]
            if best:
                t = _event_text(best)
                if t:
                    parts.append(t)
                    break

    return "\n".join(parts).strip()


def flatten_track_detail(item: Dict[str, Any]) -> str:
    """把 gettrackinfo 单条 accepted 压成邮件用的全文（含历史，仅展示不用于关键词）。"""
    number = str(item.get("number") or "")
    track_info = item.get("track_info") or {}
    lines: List[str] = [f"单号：{number}"]

    latest = track_info.get("latest_event") or {}
    if latest:
        lines.append("最新：" + _event_text(latest))

    status = track_info.get("latest_status") or {}
    if status:
        lines.append(
            "状态："
            + " ".join(
                str(status.get(k) or "")
                for k in ("status", "sub_status", "sub_status_descr")
                if status.get(k)
            )
        )

    providers = ((track_info.get("tracking") or {}).get("providers")) or []
    for prov in providers:
        name = ((prov.get("provider") or {}).get("name")) or "carrier"
        lines.append(f"—— {name} ——")
        for ev in prov.get("events") or []:
            t = _event_text(ev)
            if t:
                lines.append(t)
    return "\n".join(lines).strip()


def fetch_many(
    token: str,
    track_ids: Iterable[str],
    *,
    lang: str = "zh",
    wait_seconds: float = 4.0,
    retries: int = 4,
    retry_interval: float = 3.0,
) -> Dict[str, Dict[str, str]]:
    """
    注册 → 轮询 gettrackinfo。
    返回 track_id -> {"detail": 全文, "latest": 最新一条}；无信息的单号不出现。
    """
    ids = [str(t).strip().upper() for t in track_ids if str(t).strip()]
    if not ids:
        return {}
    if not token:
        raise RuntimeError("17track api_token 为空")

    register_numbers(token, ids)
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    pending = set(ids)
    out: Dict[str, Dict[str, str]] = {}
    for attempt in range(1, retries + 1):
        if not pending:
            break
        info = get_track_info(token, sorted(pending), lang=lang)
        for item in info.get("accepted") or []:
            num = str(item.get("number") or "").upper()
            if not num:
                continue
            detail = flatten_track_detail(item)
            if detail and "No tracking information" not in detail:
                out[num] = {
                    "detail": detail,
                    "latest": latest_match_text(item) or "",
                }
                pending.discard(num)
        # rejected: -18019909 暂无信息 → 继续等；其它记日志
        for rej in info.get("rejected") or []:
            num = str(rej.get("number") or "").upper()
            err = (rej.get("error") or {}) if isinstance(rej.get("error"), dict) else {}
            code = err.get("code")
            msg = err.get("message") or ""
            if code == -18019909:
                logger.info("17track 暂无轨迹 %s，等待重试 (%s/%s)", num, attempt, retries)
            else:
                logger.warning("17track rejected %s code=%s msg=%s", num, code, msg)
        if pending and attempt < retries:
            time.sleep(retry_interval)

    logger.info("17track 成功 %s/%s，未返回 %s", len(out), len(ids), len(pending))
    return out
