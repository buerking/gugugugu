"""物流守护旁路进程：在 big/ 下拉起 run_logistics_daemon，避免与 sub-push 的 src 命名空间冲突。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, TextIO


logger = logging.getLogger(__name__)


def _big_root() -> Path:
    from src import settings

    root = str((settings.VIP or {}).get("big_root") or "").strip()
    if root:
        return Path(root).resolve()
    return (Path(settings.APP_ROOT).resolve().parent / "big").resolve()


class LogisticsSidecar:
    """管理 big 物流守护子进程。"""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp: Optional[TextIO] = None
        self.last_error = ""

    @property
    def running(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def status_text(self) -> str:
        if self.running and self._proc is not None:
            return f"物流=运行中 pid={self._proc.pid}"
        if self.last_error:
            return f"物流=未运行 ({self.last_error[:60]})"
        return "物流=未运行"

    def start(self, *, poll_now: bool = True, scrape_now: bool = False) -> bool:
        if self.running:
            self.log.info("物流守护已在运行 pid=%s", self._proc.pid if self._proc else "?")
            return True

        big = _big_root()
        script = big / "scripts" / "run_logistics_daemon.py"
        if not script.is_file():
            self.last_error = f"找不到物流脚本: {script}"
            self.log.error("%s", self.last_error)
            return False

        log_dir = big / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "logistics_daemon.log"
        try:
            self._log_fp = open(log_path, "a", encoding="utf-8")
            self._log_fp.write(
                f"\n===== sidecar start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            )
            self._log_fp.flush()
        except Exception as exc:
            self.last_error = f"无法写物流日志: {exc}"
            self.log.error("%s", self.last_error)
            return False

        args: List[str] = [sys.executable, str(script)]
        if scrape_now:
            args.append("--scrape-now")
        if poll_now:
            args.append("--poll-now")

        creationflags = 0
        if sys.platform == "win32":
            # 独立进程组，关闭主程序时可安全 terminate
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(big),
                stdout=self._log_fp,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        except Exception as exc:
            self.last_error = str(exc)
            self.log.exception("启动物流守护失败")
            self._close_log()
            return False

        time.sleep(0.8)
        if self._proc.poll() is not None:
            self.last_error = f"物流进程立刻退出 code={self._proc.returncode}，见 {log_path}"
            self.log.error("%s", self.last_error)
            self._close_log()
            self._proc = None
            return False

        self.last_error = ""
        self.log.warning(
            "物流守护已启动 pid=%s poll_now=%s scrape_now=%s 日志=%s",
            self._proc.pid,
            poll_now,
            scrape_now,
            log_path,
        )
        return True

    def stop(self, timeout: float = 8.0) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            self._close_log()
            return
        if proc.poll() is not None:
            self._close_log()
            return
        self.log.warning("正在停止物流守护 pid=%s …", proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as exc:
            self.log.warning("停止物流守护异常: %s", exc)
        self._close_log()

    def _close_log(self) -> None:
        fp = self._log_fp
        self._log_fp = None
        if fp is None:
            return
        try:
            fp.flush()
            fp.close()
        except Exception:
            pass
