"""物流双节奏守护：每日定点 Edge 抓号 + 周期查轨迹。可被脚本或 GUI 启动/停止。"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from src import settings
from src.logistics.monitor import (
    _logistics_cfg,
    _seen_path,
    get_saved_track_ids,
    poll_and_notify,
    scrape_and_save_track_ids,
)


logger = logging.getLogger(__name__)


def load_gpu_sub() -> dict:
    with open(settings.SUBSCRIPTIONS_PATH, "r", encoding="utf-8") as f:
        subs = json.load(f)
    for s in subs:
        if s.get("logistics_monitor") or s.get("sub_user_id") == "GPU01":
            return s
    raise RuntimeError("未找到显卡用户订阅 GPU01")


def sched_cfg() -> Dict[str, Any]:
    cfg = _logistics_cfg()
    start_hour = int(cfg.get("poll_window_start_hour", 7))
    end_hour = int(cfg.get("poll_window_end_hour", 21))
    if not (0 <= start_hour < end_hour <= 24):
        start_hour, end_hour = 7, 21
    return {
        "scrape_hour": int(cfg.get("scrape_hour", 11)),
        "scrape_minute": int(cfg.get("scrape_minute", 0)),
        "poll_interval_minutes": float(cfg.get("poll_interval_minutes") or 60),
        "poll_window_start_hour": start_hour,
        "poll_window_end_hour": end_hour,
        "poll_right_after_scrape": bool(cfg.get("poll_right_after_scrape", True)),
        "tick_seconds": max(5, int(cfg.get("daemon_tick_seconds") or 20)),
    }


def in_poll_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """查轨迹时段，end_hour 不含在内。例如 7–21 表示 7:00 至 20:59。"""
    return start_hour <= now.hour < end_hour


def window_open_at_or_after(now: datetime, start_hour: int, end_hour: int) -> datetime:
    """不早于 now 的下一个查轨迹时刻。已在时段内则返回 now。"""
    if in_poll_window(now, start_hour, end_hour):
        return now
    start_today = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if now <= start_today:
        return start_today
    return start_today + timedelta(days=1)


def next_poll_datetime(
    after: datetime,
    *,
    interval_minutes: float,
    start_hour: int,
    end_hour: int,
) -> datetime:
    """上一轮结束后再隔 interval；若落在夜间则顺延到下一时段开门。"""
    candidate = after + timedelta(minutes=interval_minutes)
    if in_poll_window(candidate, start_hour, end_hour):
        return candidate
    return window_open_at_or_after(candidate, start_hour, end_hour)


def run_scrape() -> Dict[str, Any]:
    logger.info("======== 开始每日 Edge 抓号 ========")
    logger.info("若出现点击验证码：请在 Edge 内手动点选，耐心等待，不要强刷")
    result = scrape_and_save_track_ids(keep_browser_open=False)
    if result.get("login_pending"):
        logger.warning(
            "抓号暂缓（登录未通过），已有单号 %s 个将继续定时查轨迹；请人工辅助登录后自动重试抓号",
            result.get("existing_count") or len(get_saved_track_ids()),
        )
    else:
        logger.info(
            "抓号结束：共 %s 个，写入 %s",
            result.get("count") or 0,
            _seen_path(),
        )
    return result


def run_poll(sub: dict) -> Dict[str, Any]:
    ids = get_saved_track_ids()
    if not ids:
        logger.info("======== 查轨迹跳过（暂无已存单号）========")
        return {"hits": [], "sent": [], "skipped": [], "empty": True}
    logger.info("======== 开始查轨迹（单号 %s 个）========", len(ids))
    result = poll_and_notify(sub, skip_unchanged=True, dry_run=False)
    logger.info(
        "查轨迹结束：命中 %s，发送 %s，跳过 %s",
        len(result.get("hits") or []),
        len(result.get("sent") or []),
        len(result.get("skipped") or []),
    )
    return result


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%H:%M:%S")


def _fmt_next(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not dt:
        return "—"
    now = now or datetime.now()
    if dt.date() != now.date():
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%H:%M:%S")


class LogisticsDaemon:
    """后台线程：每日 11:00 抓号，7:00–21:00 每小时查轨迹。"""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # GUI 状态栏可读
        self.last_scrape_at: Optional[datetime] = None
        self.last_poll_at: Optional[datetime] = None
        self.next_poll_at: Optional[datetime] = None
        self.last_error: str = ""
        self.login_pending: bool = False
        self.poll_interval_minutes: float = 30.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status_text(self) -> str:
        if not self.running:
            return "状态：未运行"
        ids = 0
        try:
            ids = len(get_saved_track_ids())
        except Exception:
            pass
        pending = " | 待人工登录后抓号" if self.login_pending else ""
        return (
            f"状态：运行中{pending} | 单号 {ids} | "
            f"上次抓号 {_fmt_dt(self.last_scrape_at)} | "
            f"上次查轨迹 {_fmt_dt(self.last_poll_at)} | "
            f"下次查轨迹 {_fmt_next(self.next_poll_at)}"
        )

    def start(self, *, scrape_now: bool = False, poll_now: bool = False) -> None:
        with self._lock:
            if self.running:
                self.log.info("物流守护已在运行")
                return
            self._stop.clear()
            self.last_error = ""
            self.login_pending = False
            self._thread = threading.Thread(
                target=self._loop,
                kwargs={"scrape_now": scrape_now, "poll_now": poll_now},
                daemon=True,
                name="logistics-daemon",
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.log.info("物流守护停止中…")

    def scrape_once(self) -> None:
        threading.Thread(target=self._safe_scrape, daemon=True, name="logistics-scrape").start()

    def poll_once(self) -> None:
        threading.Thread(target=self._safe_poll, daemon=True, name="logistics-poll").start()

    def _safe_scrape(self) -> None:
        try:
            result = run_scrape()
            if result.get("login_pending"):
                self.login_pending = True
                self.last_error = str(result.get("error") or "登录未通过，抓号暂缓")
            else:
                self.login_pending = False
                self.last_scrape_at = datetime.now()
                self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            self.log.exception("手动抓号失败")

    def _safe_poll(self) -> None:
        try:
            run_poll(load_gpu_sub())
            self.last_poll_at = datetime.now()
            self.next_poll_at = self._schedule_after(self.last_poll_at)
            if not self.login_pending:
                self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            self.log.exception("手动查轨迹失败")

    def _do_poll(
        self,
        sub: dict,
        *,
        last_poll_at: Optional[datetime],
        poll_minutes: float,
        clear_error: bool = True,
    ) -> datetime:
        try:
            run_poll(sub)
            if clear_error and not self.login_pending:
                self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            self.log.exception("查轨迹失败")
        now = datetime.now()
        self.last_poll_at = now
        self.next_poll_at = self._schedule_after(now)
        return now

    def _schedule_after(self, after: datetime) -> datetime:
        sched = sched_cfg()
        return next_poll_datetime(
            after,
            interval_minutes=float(sched["poll_interval_minutes"]),
            start_hour=int(sched["poll_window_start_hour"]),
            end_hour=int(sched["poll_window_end_hour"]),
        )

    def _handle_scrape_result(
        self,
        result: Dict[str, Any],
        *,
        today: date,
        last_scrape_day: Optional[date],
        last_scrape_fail_at: Optional[datetime],
    ) -> tuple:
        """成功则记当日已抓；登录未过则标记 pending，稍后重试，不阻断查轨迹。"""
        if result.get("login_pending"):
            self.login_pending = True
            self.last_error = str(result.get("error") or "登录未通过，抓号暂缓")
            self.log.warning(
                "抓号待人工辅助登录；已有单号继续定时查轨迹，约 10 分钟后重试抓号"
            )
            return last_scrape_day, datetime.now()
        self.login_pending = False
        self.last_scrape_at = datetime.now()
        self.last_error = ""
        return today, None

    def _loop(self, *, scrape_now: bool, poll_now: bool) -> None:
        try:
            sub = load_gpu_sub()
        except Exception:
            self.log.exception("加载物流订阅失败，守护未启动")
            return

        sched = sched_cfg()
        scrape_h = sched["scrape_hour"]
        scrape_m = sched["scrape_minute"]
        poll_minutes = sched["poll_interval_minutes"]
        poll_start = int(sched["poll_window_start_hour"])
        poll_end = int(sched["poll_window_end_hour"])
        self.poll_interval_minutes = poll_minutes
        poll_after = sched["poll_right_after_scrape"]
        tick = sched["tick_seconds"]
        scrape_retry_seconds = 600
        heartbeat_every = 300  # 每 5 分钟打一条心跳，确认守护还活着

        last_scrape_day: Optional[date] = None
        last_poll_at: Optional[datetime] = None
        last_scrape_fail_at: Optional[datetime] = None
        last_heartbeat_at = datetime.now()

        self.log.info("=" * 60)
        self.log.info("物流双节奏守护已启动")
        self.log.info("用户: %s <%s>", sub.get("customer_name"), sub.get("user_email"))
        self.log.info("状态文件: %s", _seen_path())
        self.log.info(
            "提示: 抓号登录若弹出点击验证码，请在 Edge 窗口手动完成；启动后请耐心等待，不要强刷/关页"
        )
        self.log.info(
            "若登录未通过：将继续定时查已有单号，抓号等待人工辅助登录后自动重试"
        )
        self.log.info("每日抓号: %02d:%02d", scrape_h, scrape_m)
        self.log.info(
            "查轨迹: 每天 %02d:00–%02d:00 每 %s 分钟（%02d:00 起不再查，顺延到次日开门）",
            poll_start,
            poll_end,
            int(poll_minutes) if float(poll_minutes).is_integer() else poll_minutes,
            poll_end,
        )
        self.log.info("已有单号: %s", len(get_saved_track_ids()))
        self.log.info("=" * 60)

        if scrape_now:
            try:
                result = run_scrape()
                last_scrape_day, last_scrape_fail_at = self._handle_scrape_result(
                    result,
                    today=date.today(),
                    last_scrape_day=last_scrape_day,
                    last_scrape_fail_at=last_scrape_fail_at,
                )
            except Exception as exc:
                self.last_error = str(exc)
                self.log.exception("启动时抓号失败")
                last_scrape_fail_at = datetime.now()
            # 无论抓号是否登录通过，只要配置了立即查轨迹就继续用已有单号
            if poll_after:
                last_poll_at = self._do_poll(
                    sub, last_poll_at=last_poll_at, poll_minutes=poll_minutes
                )

        if poll_now and not (scrape_now and poll_after):
            last_poll_at = self._do_poll(
                sub, last_poll_at=last_poll_at, poll_minutes=poll_minutes
            )
        elif (
            scrape_now
            and not poll_after
            and not poll_now
            and last_poll_at is None
            and get_saved_track_ids()
        ):
            # 启动抓号失败/暂缓时，仍立刻用已有单号查一轮，避免干等到下一周期
            if self.login_pending or last_scrape_fail_at is not None:
                last_poll_at = self._do_poll(
                    sub, last_poll_at=last_poll_at, poll_minutes=poll_minutes
                )

        if last_poll_at is None:
            self.next_poll_at = window_open_at_or_after(datetime.now(), poll_start, poll_end)
            if not in_poll_window(datetime.now(), poll_start, poll_end):
                self.log.info("当前不在查轨迹时段，下次 %s", _fmt_next(self.next_poll_at))

        while not self._stop.is_set():
            now = datetime.now()
            today = now.date()
            target = now.replace(hour=scrape_h, minute=scrape_m, second=0, microsecond=0)

            need_scrape = now >= target and last_scrape_day != today
            if need_scrape and last_scrape_fail_at is not None:
                if (now - last_scrape_fail_at).total_seconds() < scrape_retry_seconds:
                    need_scrape = False
            if need_scrape:
                try:
                    result = run_scrape()
                    last_scrape_day, last_scrape_fail_at = self._handle_scrape_result(
                        result,
                        today=today,
                        last_scrape_day=last_scrape_day,
                        last_scrape_fail_at=last_scrape_fail_at,
                    )
                    # 登录未过也继续用已有单号查轨迹
                    if poll_after:
                        last_poll_at = self._do_poll(
                            sub,
                            last_poll_at=last_poll_at,
                            poll_minutes=poll_minutes,
                            clear_error=not self.login_pending,
                        )
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log.exception(
                        "每日抓号失败，%s 分钟后再试；已有单号继续查轨迹",
                        scrape_retry_seconds // 60,
                    )
                    last_scrape_fail_at = now
                    if poll_after and get_saved_track_ids():
                        last_poll_at = self._do_poll(
                            sub,
                            last_poll_at=last_poll_at,
                            poll_minutes=poll_minutes,
                            clear_error=False,
                        )

            due_poll = False
            if not in_poll_window(now, poll_start, poll_end):
                opened = window_open_at_or_after(now, poll_start, poll_end)
                if self.next_poll_at is None or self.next_poll_at < opened:
                    self.next_poll_at = opened
            elif get_saved_track_ids() and (
                last_poll_at is None
                or (self.next_poll_at is not None and now >= self.next_poll_at)
            ):
                due_poll = True

            if due_poll:
                last_poll_at = self._do_poll(
                    sub,
                    last_poll_at=last_poll_at,
                    poll_minutes=poll_minutes,
                    clear_error=not self.login_pending,
                )
                self.log.info(
                    "本轮查轨迹完成，下次约 %s%s",
                    _fmt_next(self.next_poll_at),
                    "（抓号待人工登录）" if self.login_pending else "",
                )

            # 心跳：即使无任务也说明守护活着
            if (now - last_heartbeat_at).total_seconds() >= heartbeat_every:
                last_heartbeat_at = now
                nxt = _fmt_next(self.next_poll_at) if self.next_poll_at else "待定"
                pending = " | 待人工登录后抓号" if self.login_pending else ""
                self.log.info(
                    "[心跳] 守护运行中%s | 单号=%s | 上次查轨迹=%s | 下次约=%s",
                    pending,
                    len(get_saved_track_ids()),
                    _fmt_dt(self.last_poll_at),
                    nxt,
                )

            end = time.time() + tick
            while time.time() < end:
                if self._stop.is_set():
                    break
                time.sleep(0.3)

        self.next_poll_at = None
        self.log.info("物流守护已停止")
