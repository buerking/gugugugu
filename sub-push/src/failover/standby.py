"""开发端灾备：按生产 IP 请求 /health，用最近请求时间判断是否失联。"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from src import settings
from src.failover.lease import default_node_id
from src.notify import feishu
from src.pipeline import storage
from src.unified_cycle import UnifiedRunner

logger = logging.getLogger(__name__)


def _cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "FAILOVER", None) or {})


def probe_primary(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    返回 (alive, reason, health_json)。
    - 连不上 → 死亡
    - 连上但 activity_age_seconds > stale → 死亡（进程在但业务停了）
    - alive 字段 / 年龄正常 → 存活
    """
    cfg = cfg or _cfg()
    url = str(cfg.get("standby_primary_url") or "").strip()
    if not url:
        raise RuntimeError(
            "请配置 failover.standby_primary_url，例如 http://192.168.1.10:18765/health"
        )
    timeout = float(cfg.get("http_timeout_seconds") or 5)
    stale = float(cfg.get("stale_seconds") or 180)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(resp.status)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"http_unreachable:{exc}", {}

    if not (200 <= status < 300):
        return False, f"http_status:{status}", {}

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return False, "bad_json", {}

    if not isinstance(data, dict):
        return False, "bad_payload", {}

    age = data.get("activity_age_seconds")
    if age is None:
        # 兼容极简 health
        age = 0.0
    try:
        age_f = float(age)
    except (TypeError, ValueError):
        age_f = 999999.0

    if data.get("alive") is False or age_f > stale:
        return (
            False,
            f"stale_activity age={age_f:.0f}s>{stale:.0f}s",
            data,
        )
    return True, "ok", data


def primary_is_alive(cfg: Optional[Dict[str, Any]] = None) -> bool:
    alive, _reason, _data = probe_primary(cfg)
    return alive


def run_standby(*, log: Optional[logging.Logger] = None) -> None:
    log = log or logger
    cfg = _cfg()
    if not cfg.get("enabled"):
        raise SystemExit("failover.enabled=false，灾备监控未开启")

    interval = max(
        5.0, float(cfg.get("check_interval_seconds") or cfg.get("interval_seconds") or 30)
    )
    need_fail = max(1, int(cfg.get("fail_checks_before_takeover") or 2))
    node = str(cfg.get("node_id") or default_node_id())
    url = str(cfg.get("standby_primary_url") or "").strip()

    storage.ensure_dirs()
    runner = UnifiedRunner(log=log)
    active = False
    fail_streak = 0
    ok_streak = 0

    log.warning(
        "灾备监控启动 node=%s url=%s stale=%ss need_fail=%s",
        node,
        url or "-",
        cfg.get("stale_seconds") or 180,
        need_fail,
    )
    try:
        feishu.send_text(
            f"🛡️ 灾备监控已启动\nnode={node}\n探测={url}\n失联将自动接管。",
            tag="failover-monitor-start",
        )
    except Exception:
        pass

    try:
        while True:
            cfg = _cfg()
            try:
                alive, reason, data = probe_primary(cfg)
            except Exception as exc:
                log.error("探测配置错误: %s", exc)
                alive, reason, data = False, str(exc), {}

            age = data.get("activity_age_seconds")
            last_iso = data.get("last_request_at_iso") or data.get("activity_at_iso")
            kind = data.get("last_request_kind") or "-"

            if alive:
                fail_streak = 0
                ok_streak += 1
                if active and ok_streak >= max(
                    1, int(cfg.get("ok_checks_before_release") or 2)
                ):
                    log.warning("生产 health 恢复，灾备让出（停止本机守护）")
                    try:
                        runner.stop()
                    except Exception:
                        pass
                    active = False
                    try:
                        feishu.send_text(
                            f"✅ 灾备已让出\nnode={node}\n生产端 health 恢复。",
                            tag="failover-release",
                        )
                    except Exception:
                        pass
                log.info(
                    "生产存活 age=%ss last=%s kind=%s active=%s",
                    age,
                    last_iso,
                    kind,
                    active,
                )
            else:
                ok_streak = 0
                fail_streak += 1
                log.warning(
                    "生产异常 reason=%s fail=%s/%s age=%s last=%s",
                    reason,
                    fail_streak,
                    need_fail,
                    age,
                    last_iso,
                )
                if not active and fail_streak >= need_fail:
                    log.warning("接管：启动本机统一守护")
                    runner.start(run_baseline=True)
                    active = True
                    try:
                        feishu.send_text(
                            f"🚨 灾备已接管\nnode={node}\nreason={reason}\n"
                            f"连续 {fail_streak} 次探测失败。",
                            tag="failover-takeover",
                        )
                    except Exception:
                        pass

            time.sleep(interval)
    except KeyboardInterrupt:
        log.warning("灾备监控退出")
        if active:
            try:
                runner.stop()
            except Exception:
                pass
        try:
            feishu.send_text(
                f"灾备监控已停止\nnode={node}",
                tag="failover-monitor-stop",
            )
        except Exception:
            pass
