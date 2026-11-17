"""上架/更新时间窗，对齐 sub-push 的 build_created_after 设计。"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional


def build_created_after(
    schedule_interval_minutes: float,
    first_run: bool,
    subscription: Dict[str, Any],
    *,
    last_success_at: Optional[int] = None,
    overlap_seconds: int = 60,
) -> int:
    """
    计算时间窗阈值（秒级 timestamp）。

    - 首轮：订阅 created_time，否则最近 24h
    - 非首轮优先用「上次搜索成功返回」时间（减去 overlap，防边界漏单）
    - 无成功水位时回退：现在 − 1.5×调度间隔
    """
    if first_run:
        raw = subscription.get("created_time")
        try:
            return int(float(raw))
        except Exception:
            return int((datetime.datetime.now() - datetime.timedelta(hours=24)).timestamp())

    now = datetime.datetime.now()
    fallback_minutes = max(0.1, float(schedule_interval_minutes) * 1.5)
    fallback_ts = int((now - datetime.timedelta(minutes=fallback_minutes)).timestamp())

    if last_success_at is not None:
        try:
            ts = int(last_success_at) - max(0, int(overlap_seconds))
            # 取更早阈值（更大窗口）：故障恢复后能补上水位之后的单；正常时也不窄于 1.5×间隔
            return min(ts, fallback_ts)
        except Exception:
            pass
    return fallback_ts


def _to_ts(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        # PayPay / Yahoo 可能是 ISO 字符串
        if isinstance(value, str) and ("T" in value or "-" in value):
            s = value.replace("Z", "+00:00")
            return int(datetime.datetime.fromisoformat(s).timestamp())
        return int(float(value))
    except Exception:
        return None


def item_in_time_window(item: Dict[str, Any], threshold_ts: int) -> bool:
    """
    created 或 updated 任一落在时间窗内即保留。
    （煤炉常见：很早 created、近期 updated 降价再曝光）
    """
    created = _to_ts(item.get("created"))
    updated = _to_ts(item.get("updated") or item.get("end_time"))
    times = [t for t in (created, updated) if t is not None]
    if not times:
        # 无时间字段时保守保留，交给去重/AI
        return True
    return max(times) >= threshold_ts
