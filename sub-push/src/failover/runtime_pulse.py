"""内存心跳：记录最近一次对外请求时间（不落盘）。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class RuntimePulse:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.last_request_at = 0.0
        self.last_request_kind = ""
        self.last_request_detail = ""

    def note(self, kind: str, detail: str = "") -> None:
        with self._lock:
            self.last_request_at = time.time()
            self.last_request_kind = str(kind or "")[:64]
            self.last_request_detail = str(detail or "")[:240]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            last = float(self.last_request_at or 0)
            started = float(self.started_at or now)
            # 尚未发出请求时，用进程启动时间充当“最近活动”，避免刚启动被误判死亡
            activity_at = last if last > 0 else started
            age = max(0.0, now - activity_at)
            return {
                "started_at": started,
                "started_at_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(started)
                ),
                "last_request_at": last if last > 0 else None,
                "last_request_at_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last))
                    if last > 0
                    else None
                ),
                "last_request_kind": self.last_request_kind or None,
                "last_request_detail": self.last_request_detail or None,
                "activity_at": activity_at,
                "activity_at_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(activity_at)
                ),
                "activity_age_seconds": round(age, 1),
                "has_outbound_request": last > 0,
            }


PULSE = RuntimePulse()


def note_request(kind: str, detail: str = "") -> None:
    PULSE.note(kind, detail)


def health_payload(
    *,
    node_id: str,
    role: str = "primary",
    stale_seconds: float = 180,
) -> Dict[str, Any]:
    snap = PULSE.snapshot()
    age = float(snap.get("activity_age_seconds") or 0)
    stale = float(stale_seconds or 180)
    alive = age <= stale
    return {
        "ok": True,
        "alive": alive,
        "role": role,
        "node_id": node_id,
        "server_time": time.time(),
        "stale_seconds": stale,
        **snap,
    }
