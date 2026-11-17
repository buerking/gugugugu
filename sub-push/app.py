"""
统一守护 GUI：左侧只展示线上公共订阅明细；VIP 只打少量日志。
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List

from src import settings
from src.runtime_board import BOARD
from src.subscriptions_index import SearchJob, load_search_jobs
from src.unified_cycle import UnifiedRunner


class GuiConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        if getattr(settings, "GUI_VERBOSE", False) and record.levelno >= logging.INFO:
            return True
        return bool(getattr(record, "gui_console", False))


class TkQueueHandler(logging.Handler):
    def __init__(self, q: "queue.Queue[str]"):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("上新统一守护")
        self.geometry("1100x720")
        self.configure(bg="#f3f1ec")

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._setup_logging()
        self.logger = logging.getLogger("unified-gui")
        self.runner = UnifiedRunner(log=self.logger)
        self.jobs: List[SearchJob] = []

        self._build_ui()
        self._poll_logs()
        self._poll_status()
        self._poll_board()
        self.refresh_jobs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
        if not any(isinstance(h, TkQueueHandler) for h in root.handlers):
            handler = TkQueueHandler(self.log_queue)
            handler.addFilter(GuiConsoleFilter())
            handler.setFormatter(
                logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S")
            )
            root.addHandler(handler)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#f3f1ec")
        style.configure("TLabel", background="#f3f1ec", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Hint.TLabel", foreground="#5c5a55", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)

        head = ttk.Frame(self)
        head.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(head, text="上新统一守护", style="Title.TLabel").pack(side="left")
        ttk.Label(
            head,
            text="中间层混编  ·  公共→后端  ·  VIP→AI邮件（不展示明细）",
            style="Hint.TLabel",
        ).pack(side="left", padx=12)

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16, pady=6)
        ttk.Button(top, text="启动守护", command=self.runner.start).pack(side="left")
        ttk.Button(top, text="暂停", command=self.runner.pause).pack(side="left", padx=8)
        ttk.Button(top, text="关闭软件", command=self._on_close).pack(side="left", padx=8)
        ttk.Button(top, text="刷新公共订阅", command=self.refresh_jobs).pack(side="right")

        self.status_var = tk.StringVar(value="状态=未启动")
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=16, pady=(0, 2))

        vip = settings.VIP or {}
        self.meta_var = tk.StringVar(
            value=(
                f"间隔 {settings.SCHEDULE_INTERVAL_MINUTES} 分钟  ·  "
                f"VIP {'开' if vip.get('enabled') else '关'}  ·  "
                f"并发上限 {(settings.SEARCH_POOL or {}).get('max_workers', '-')}"
            )
        )
        ttk.Label(self, textvariable=self.meta_var, style="Hint.TLabel").pack(
            fill="x", padx=16, pady=(0, 8)
        )

        mid = ttk.PanedWindow(self, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        left = ttk.Frame(mid)
        right = ttk.Frame(mid)
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="线上公共订阅").pack(side="left")
        self.job_count_var = tk.StringVar(value="0 条")
        ttk.Label(bar, textvariable=self.job_count_var, style="Hint.TLabel").pack(
            side="right"
        )
        ttk.Button(bar, text="清空统计", command=self.clear_local_stats).pack(
            side="right", padx=8
        )

        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(fill="both", expand=True)
        cols = (
            "subscriptionId",
            "matchType",
            "site",
            "target",
            "status",
            "raw",
            "fresh",
            "reported",
            "error",
        )
        self.job_tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=20)
        headings = {
            "subscriptionId": "订阅ID",
            "matchType": "类型",
            "site": "站点",
            "target": "关键词/卖家",
            "status": "状态",
            "raw": "接口返回",
            "fresh": "新货",
            "reported": "上报",
            "error": "错误",
        }
        widths = {
            "subscriptionId": 90,
            "matchType": 56,
            "site": 88,
            "target": 160,
            "status": 64,
            "raw": 48,
            "fresh": 48,
            "reported": 48,
            "error": 140,
        }
        for c in cols:
            self.job_tree.heading(c, text=headings[c])
            self.job_tree.column(c, width=widths[c], anchor="w")
        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.job_tree.yview)
        self.job_tree.configure(yscrollcommand=yscroll.set)
        self.job_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        ttk.Label(right, text="控制台").pack(anchor="w", pady=(0, 6))
        ttk.Label(
            right,
            text=(
                "测试详细日志（gui_verbose）；后期可关"
                if getattr(settings, "GUI_VERBOSE", False)
                else "仅错误与轮次耗时；VIP 只留摘要"
            ),
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        self.log_text = tk.Text(
            right,
            wrap="word",
            height=10,
            bg="#1e1f1c",
            fg="#e8e6e1",
            insertbackground="#e8e6e1",
            font=("Consolas", 10),
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        ttk.Button(right, text="清空日志", command=self.clear_logs).pack(
            anchor="w", pady=8
        )

    def refresh_jobs(self) -> None:
        try:
            from src.subscription_middle import get_middle
            from src.vip.loader import load_vip_jobs

            middle = get_middle()
            if middle.enabled():
                meta = middle.refresh_from_api()
                jobs = middle.public_only_jobs()
                pub = int(meta.get("public_jobs") or len(jobs))
                vip = int(meta.get("vip_jobs") or 0)
                mixed = int(meta.get("jobs") or 0)
            else:
                jobs, _, meta = load_search_jobs(
                    include_sellers=True, include_vip=False
                )
                pub = int(meta.get("subscription_count") or len(jobs))
                vip = len(load_vip_jobs())
                mixed = pub
            self.jobs = jobs
            BOARD.replace_subscription_skeleton(jobs)
        except Exception as exc:
            self.logger.exception("刷新订阅失败: %s", exc)
            messagebox.showerror("刷新订阅失败", str(exc))
            return
        self._render_board()
        slices = 0
        try:
            slices = int(get_middle().stats().get("slices") or 0)
        except Exception:
            slices = 0
        self.job_count_var.set(
            f"公共 {pub} + VIP {vip} = {mixed} 条 / {slices} 片（混编同池，VIP不展示）"
        )

    def clear_local_stats(self) -> None:
        BOARD.clear_stats()
        self._render_board()

    def _public_rows(self) -> List[Dict[str, Any]]:
        rows = list((BOARD.snapshot().get("rows") or []))
        out = []
        for row in rows:
            if str(row.get("matchType") or "") == "VIP":
                continue
            sid = str(row.get("subscriptionId") or "")
            if sid.startswith("vip:"):
                continue
            out.append(row)
        return out

    def _render_board(self) -> None:
        rows = self._public_rows()
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)
        for i, row in enumerate(rows):
            self.job_tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    row.get("subscriptionId", ""),
                    row.get("matchType", ""),
                    row.get("site", ""),
                    row.get("target", ""),
                    row.get("status", ""),
                    row.get("rawCount", 0),
                    row.get("freshCount", 0),
                    row.get("reportedCount", 0),
                    row.get("error", ""),
                ),
            )

    def _poll_board(self) -> None:
        try:
            self._render_board()
        except Exception:
            pass
        self.after(1000, self._poll_board)

    def _poll_status(self) -> None:
        try:
            self.status_var.set(self.runner.status_text())
        except Exception:
            pass
        self.after(1000, self._poll_status)

    def _on_close(self) -> None:
        try:
            self.runner.stop()
        except Exception:
            pass
        self.destroy()

    def _poll_logs(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(150, self._poll_logs)

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
