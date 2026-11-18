"""共享心跳租约：两边读同一同步目录里的 JSON。"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from typing import Any, Dict, Optional


def default_node_id() -> str:
    return socket.gethostname() or "unknown-node"


def heartbeat_path(shared_dir: str, filename: str = "failover_heartbeat.json") -> str:
    return os.path.join(shared_dir, filename)


def read_lease(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_lease(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".hb_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_lease(
    *,
    role: str,
    node_id: str,
    priority: int,
    status: str = "alive",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "role": role,
        "node_id": node_id,
        "priority": int(priority),
        "status": status,
        "ts": time.time(),
        "pid": os.getpid(),
    }
    if extra:
        body.update(extra)
    return body


def is_fresh(lease: Optional[Dict[str, Any]], stale_seconds: float) -> bool:
    if not lease:
        return False
    try:
        ts = float(lease.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    if ts <= 0:
        return False
    return (time.time() - ts) <= float(stale_seconds)


def is_primary_lease(lease: Optional[Dict[str, Any]]) -> bool:
    if not lease:
        return False
    role = str(lease.get("role") or "").lower()
    return role in ("primary", "prod", "production")
