"""临时切换到 big 的 src 包，调用 AI / 邮件，用完恢复 sub-push。"""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from typing import Dict, Iterator

from src import settings


logger = logging.getLogger(__name__)

# VIP AI 切 big 的 src 时会掏空 sys.modules['src.*']；
# 与中间层刷新等 sub-push 导入互斥，避免 import 到 big 的 pipeline。
_SRC_LOCK = threading.RLock()


def _big_root() -> str:
    cfg = getattr(settings, "VIP", None) or {}
    return str(cfg.get("big_root") or "").strip()


@contextmanager
def hold_subpush_src() -> Iterator[None]:
    """占用锁，保证期间不会切到 big src。"""
    with _SRC_LOCK:
        yield


@contextmanager
def use_big_src() -> Iterator[None]:
    root = _big_root()
    if not root:
        raise RuntimeError("VIP.big_root 未配置")

    # 锁模块本身不能卸：其它线程可能正等 hold_subpush_src（已绑定到本模块）
    _keep = {"src.vip.big_bridge"}

    with _SRC_LOCK:
        stashed: Dict[str, object] = {}
        for key in list(sys.modules):
            if key in _keep:
                continue
            if key == "src" or key.startswith("src."):
                stashed[key] = sys.modules.pop(key)

        old_path = list(sys.path)
        sys.path.insert(0, root)
        try:
            yield
        finally:
            for key in list(sys.modules):
                if key in _keep:
                    continue
                if key == "src" or key.startswith("src."):
                    sys.modules.pop(key, None)
            sys.modules.update(stashed)
            sys.path[:] = old_path
