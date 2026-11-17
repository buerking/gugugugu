"""
sub-push 守护运行时：定时执行 + 故障重试 + 心跳超时告警 + 飞书通知。

设计：
- 一轮失败：按 retry 次数退避重试；仍失败则飞书告警，不退出循环
- 连续失败达到阈值：额外飞书（冷却内不刷屏）
- 恢复成功：可选发一条恢复通知
- 心跳：若超过 stale 秒仍无成功轮次 → 飞书「疑似卡死」
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from src import settings
from src.notify import feishu


logger = logging.getLogger(__name__)


def _wd_cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "WATCHDOG", None) or {})


@dataclass
class WatchdogStatus:
    running: bool = False
    paused: bool = False
    consecutive_failures: int = 0
    total_rounds: int = 0
    total_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_error: str = ""
    last_round_seconds: float = 0.0
    next_run_at: float = 0.0
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "running": self.running,
            "paused": self.paused,
            "consecutive_failures": self.consecutive_failures,
            "total_rounds": self.total_rounds,
            "total_failures": self.total_failures,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "last_round_seconds": self.last_round_seconds,
            "next_run_at": self.next_run_at,
            "seconds_since_success": (
                (now - self.last_success_at) if self.last_success_at else None
            ),
            "uptime_seconds": now - self.started_at,
        }


class WatchdogRunner:
    def __init__(
        self,
        job: Callable[[], None],
        *,
        log: Optional[logging.Logger] = None,
        interval_seconds: Optional[float] = None,
    ) -> None:
        self.job = job
        self.log = log or logger
        self.status = WatchdogStatus()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._thread: Optional[threading.Thread] = None
        self._hb_thread: Optional[threading.Thread] = None
        cfg = _wd_cfg()
        default_interval = max(1, int(settings.SCHEDULE_INTERVAL_MINUTES)) * 60
        self.interval_seconds = float(
            interval_seconds
            if interval_seconds is not None
            else cfg.get("interval_seconds") or default_interval
        )

    def start(self, *, run_baseline: bool = False) -> None:
        # 已在跑且暂停 → 启动按钮直接恢复
        if self._thread and self._thread.is_alive():
            if self.status.paused:
                self.resume()
            return
        self._stop.clear()
        self._pause.set()
        self.status.running = True
        self.status.paused = False
        self.status.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, args=(run_baseline,), daemon=True)
        self._thread.start()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        self.log.warning(
            "守护已启动 interval=%ss retry=%s stale=%ss",
            int(self.interval_seconds),
            _wd_cfg().get("max_retries_per_round", 2),
            _wd_cfg().get("stale_alert_seconds", 1800),
        )
        if _wd_cfg().get("notify_on_start", True):
            feishu.send_text(
                f"守护进程已启动\n间隔={int(self.interval_seconds)}秒",
                tag="start",
            )

    def pause(self) -> None:
        self._pause.clear()
        self.status.paused = True
        self.log.info("守护已暂停")

    def resume(self) -> None:
        self._pause.set()
        self.status.paused = False
        self.log.info("守护已继续")

    def stop(self) -> None:
        self._stop.set()
        self._pause.set()
        self.status.running = False
        self.status.paused = False
        self.log.info("守护已停止")
        if _wd_cfg().get("notify_on_stop", False):
            feishu.send_text("守护进程已停止", tag="stop")

    def run_once_now(self) -> None:
        self.log.info("手动触发一轮")
        self._execute_round(label="manual")

    def _execute_round(self, *, label: str = "scheduled") -> bool:
        cfg = _wd_cfg()
        max_retries = max(0, int(cfg.get("max_retries_per_round") or 2))
        base_delay = float(cfg.get("retry_delay_seconds") or 15)
        fail_notify_after = max(1, int(cfg.get("fail_notify_after") or 1))

        attempt = 0
        last_exc: Optional[BaseException] = None
        t0 = time.time()
        while attempt <= max_retries:
            attempt += 1
            try:
                self.job()
                elapsed = time.time() - t0
                self.status.total_rounds += 1
                self.status.last_round_seconds = elapsed
                self.status.last_success_at = time.time()
                self.status.last_error = ""
                if self.status.consecutive_failures > 0:
                    feishu.notify_recovered(
                        f"连续失败 {self.status.consecutive_failures} 次后已恢复\n"
                        f"本轮耗时={elapsed:.1f}s label={label}"
                    )
                self.status.consecutive_failures = 0
                self.log.info(
                    "本轮耗时=%.1fs",
                    elapsed,
                    extra={"gui_console": True},
                )
                return True
            except Exception as exc:
                last_exc = exc
                self.log.exception(
                    "一轮失败 label=%s attempt=%s/%s: %s",
                    label,
                    attempt,
                    max_retries + 1,
                    exc,
                )
                if attempt <= max_retries:
                    delay = base_delay * attempt
                    self.log.warning("将在 %.0fs 后重试…", delay)
                    # 可被 stop 打断
                    end = time.time() + delay
                    while time.time() < end:
                        if self._stop.is_set():
                            break
                        time.sleep(0.2)

        self.status.total_rounds += 1
        self.status.total_failures += 1
        self.status.consecutive_failures += 1
        self.status.last_failure_at = time.time()
        self.status.last_error = str(last_exc or "unknown")[:500]
        self.status.last_round_seconds = time.time() - t0

        if self.status.consecutive_failures >= fail_notify_after:
            feishu.notify_failure(
                f"上新搜索轮次失败（已重试 {max_retries} 次）",
                detail=(
                    f"连续失败={self.status.consecutive_failures}\n"
                    f"label={label}\n"
                    f"错误={self.status.last_error}"
                ),
                tag="round_fail",
            )
        return False

    def _loop(self, run_baseline: bool) -> None:
        if run_baseline and not self._stop.is_set():
            self.log.info("启动首轮建基线…")
            try:
                # 基线失败也告警，但不阻塞后续
                from src.main import run_once

                run_once(first_run=True)
                self.status.last_success_at = time.time()
            except Exception as exc:
                self.log.exception("首轮建基线失败: %s", exc)
                feishu.notify_failure(
                    "首轮建基线失败",
                    detail=str(exc)[:800],
                    tag="baseline_fail",
                )

        self.status.next_run_at = time.time()
        while not self._stop.is_set():
            self._pause.wait(0.2)
            if self._stop.is_set():
                break
            if self.status.paused:
                time.sleep(0.3)
                continue
            now = time.time()
            if now < self.status.next_run_at:
                time.sleep(0.3)
                continue
            self._execute_round(label="scheduled")
            self.status.next_run_at = time.time() + self.interval_seconds

        self.status.running = False

    def _heartbeat_loop(self) -> None:
        cfg = _wd_cfg()
        check_every = float(cfg.get("heartbeat_check_seconds") or 60)
        stale = float(cfg.get("stale_alert_seconds") or 1800)
        alive_every = float(cfg.get("alive_notify_seconds") or 0)  # 0=不发周期存活
        last_alive = time.time()
        while not self._stop.is_set():
            time.sleep(min(5.0, check_every))
            if self._stop.is_set() or not self.status.running or self.status.paused:
                continue
            now = time.time()
            # 疑似卡死：过久无成功
            ref = self.status.last_success_at or self.status.started_at
            if stale > 0 and now - ref >= stale:
                feishu.notify_failure(
                    "心跳超时：过久没有成功完成一轮",
                    detail=(
                        f"已等待={int(now - ref)}秒（阈值={int(stale)}秒）\n"
                        f"连续失败={self.status.consecutive_failures}\n"
                        f"上次错误={self.status.last_error or '-'}"
                    ),
                    tag="stale",
                )
            if alive_every > 0 and now - last_alive >= alive_every:
                snap = self.status.snapshot()
                feishu.notify_heartbeat(
                    f"uptime={int(snap['uptime_seconds'])}s "
                    f"rounds={snap['total_rounds']} "
                    f"fail_streak={snap['consecutive_failures']}"
                )
                last_alive = now
