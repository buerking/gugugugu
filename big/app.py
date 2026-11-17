"""简易 GUI：商品推送 + 物流双节奏监控（双日志窗口）。"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
from src import settings
from src.logistics.daemon import LogisticsDaemon
from src.pipeline.processor import run_once


def _is_logistics_record(record: logging.LogRecord) -> bool:
    name = record.name or ""
    if name.startswith("src.logistics") or name == "logistics":
        return True
    msg = record.getMessage() if hasattr(record, "getMessage") else ""
    if name.startswith("src.") and any(k in msg for k in ("物流", "17TRACK", "抓号", "查轨迹")):
        return True
    return False


class RouteHandler(logging.Handler):
    """按 logger 名分流到搜索推送 / 物流 两个队列。"""

    def __init__(self, push_q: "queue.Queue[str]", logi_q: "queue.Queue[str]"):
        super().__init__()
        self.push_q = push_q
        self.logi_q = logi_q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            if _is_logistics_record(record):
                self.logi_q.put_nowait(text)
            else:
                self.push_q.put_nowait(text)
        except Exception:
            pass


class Scheduler:
    def __init__(self, log: logging.Logger):
        self.log = log
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._pause.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log.info("推送任务已启动，间隔=%s秒", settings.SCHEDULE_INTERVAL_SECONDS)

    def pause(self) -> None:
        self._pause.clear()
        self.log.info("推送任务已暂停")

    def resume(self) -> None:
        self._pause.set()
        self.log.info("推送任务已继续")

    def stop(self) -> None:
        self._stop.set()
        self._pause.set()
        self.log.info("推送任务已停止")

    def run_once_now(self, first_run: bool = False) -> None:
        self.log.info("手动执行一次 first_run=%s", first_run)
        run_once(first_run=first_run)

    def _loop(self) -> None:
        interval = max(1, int(settings.SCHEDULE_INTERVAL_SECONDS))
        first = True
        while not self._stop.is_set():
            self._pause.wait(0.2)
            if self._stop.is_set():
                break
            try:
                run_once(first_run=first)
                first = False
            except Exception as e:
                self.log.exception("执行失败: %s", e)
            end = time.time() + interval
            while time.time() < end:
                if self._stop.is_set():
                    return
                self._pause.wait(0.2)
                time.sleep(0.3)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("big 专供推送")
        self.geometry("1100x720")

        self.push_q: queue.Queue[str] = queue.Queue()
        self.logi_q: queue.Queue[str] = queue.Queue()

        level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        route = RouteHandler(self.push_q, self.logi_q)
        route.setFormatter(fmt)
        route.setLevel(level)

        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(route)
        root.setLevel(level)

        self.push_logger = logging.getLogger("big.push")
        self.logi_logger = logging.getLogger("src.logistics.daemon")
        self.push_logger.setLevel(level)
        self.logi_logger.setLevel(level)

        self.scheduler = Scheduler(self.push_logger)
        self.logistics = LogisticsDaemon(self.logi_logger)

        # —— 商品推送 ——
        push = ttk.LabelFrame(self, text="商品搜索推送")
        push.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(push, text="启动", command=self.scheduler.start).pack(side="left", padx=4, pady=6)
        ttk.Button(push, text="暂停", command=self.scheduler.pause).pack(side="left", padx=4)
        ttk.Button(push, text="继续", command=self.scheduler.resume).pack(side="left", padx=4)
        ttk.Button(push, text="停止", command=self.scheduler.stop).pack(side="left", padx=4)
        ttk.Button(
            push,
            text="立即执行(增量)",
            command=lambda: threading.Thread(
                target=self.scheduler.run_once_now, kwargs={"first_run": False}, daemon=True
            ).start(),
        ).pack(side="left", padx=12)
        ttk.Button(
            push,
            text="建基线(不推送)",
            command=lambda: threading.Thread(
                target=self.scheduler.run_once_now, kwargs={"first_run": True}, daemon=True
            ).start(),
        ).pack(side="left", padx=4)

        # —— 物流监控 ——
        logi = ttk.LabelFrame(self, text="物流监控（每日11:00抓号 / 7:00–21:00每小时查轨迹）")
        logi.pack(fill="x", padx=8, pady=4)
        ttk.Button(logi, text="启动物流守护", command=self._start_logistics).pack(
            side="left", padx=4, pady=6
        )
        ttk.Button(logi, text="停止物流守护", command=self.logistics.stop).pack(side="left", padx=4)
        ttk.Button(logi, text="立即抓号", command=self.logistics.scrape_once).pack(side="left", padx=12)
        ttk.Button(logi, text="立即查轨迹", command=self.logistics.poll_once).pack(side="left", padx=4)
        self.logi_status = ttk.Label(logi, text="状态：未运行")
        self.logi_status.pack(side="left", padx=16)

        # —— 双日志 ——
        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        ttk.Label(left, text="搜索推送日志").pack(anchor="w")
        self.push_text = scrolledtext.ScrolledText(left, height=28, wrap="word")
        self.push_text.pack(fill="both", expand=True)

        ttk.Label(right, text="物流监控日志（看心跳 / 查轨迹完成）").pack(anchor="w")
        self.logi_text = scrolledtext.ScrolledText(right, height=28, wrap="word")
        self.logi_text.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._drain_logs)
        self.after(1000, self._refresh_status)

        self.push_logger.info("GUI 已启动。左侧=搜索推送，右侧=物流。")
        self.logi_logger.info("物流日志窗口已就绪。请点击「启动物流守护」。")

    def _start_logistics(self) -> None:
        self.logistics.start(scrape_now=False, poll_now=False)
        self._refresh_status()

    def _on_close(self) -> None:
        try:
            self.scheduler.stop()
            self.logistics.stop()
        except Exception:
            pass
        self.destroy()

    def _append(self, widget: scrolledtext.ScrolledText, msg: str) -> None:
        widget.insert("end", msg + "\n")
        # 控制缓冲，避免日志过长卡顿
        try:
            line_count = int(widget.index("end-1c").split(".")[0])
            if line_count > 4000:
                widget.delete("1.0", "1000.0")
        except Exception:
            pass
        widget.see("end")

    def _drain_logs(self) -> None:
        while True:
            try:
                msg = self.push_q.get_nowait()
            except queue.Empty:
                break
            self._append(self.push_text, msg)
        while True:
            try:
                msg = self.logi_q.get_nowait()
            except queue.Empty:
                break
            self._append(self.logi_text, msg)
        self.after(300, self._drain_logs)

    def _refresh_status(self) -> None:
        text = self.logistics.status_text()
        if self.logistics.last_error:
            text += f" | 最近错误: {self.logistics.last_error[:80]}"
        self.logi_status.configure(text=text)
        self.after(1000, self._refresh_status)


if __name__ == "__main__":
    App().mainloop()
