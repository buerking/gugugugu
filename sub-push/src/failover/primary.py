"""生产端：对外提供 /health（内存最近请求时间），供开发端按 IP 探测。"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from src import settings
from src.failover.lease import default_node_id
from src.failover.runtime_pulse import health_payload

logger = logging.getLogger(__name__)


class PrimaryHeartbeat:
    """兼容旧名：现在只开 HTTP health，不再写网盘文件。"""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        cfg = dict(getattr(settings, "FAILOVER", None) or {})
        if not cfg.get("enabled"):
            return
        role = str(cfg.get("role") or "primary").lower()
        if role not in ("primary", "prod", "production", "auto"):
            return
        if self._httpd is not None:
            return
        self._start_http(cfg)

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None

    def _start_http(self, cfg: Dict[str, Any]) -> None:
        http_cfg = dict(cfg.get("http_health") or {})
        # 生产启用灾备时默认开 health；仅当显式 enabled=false 才关
        if "enabled" in http_cfg and not http_cfg.get("enabled"):
            self.log.warning("failover.http_health.enabled=false，未启动 /health")
            return
        host = str(http_cfg.get("host") or "0.0.0.0")
        port = int(http_cfg.get("port") or 18765)
        node = str(cfg.get("node_id") or default_node_id())
        stale = float(cfg.get("stale_seconds") or 180)
        role = str(cfg.get("role") or "primary")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if path not in ("/health", "/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                body_obj = health_payload(
                    node_id=node, role=role, stale_seconds=stale
                )
                raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
                # alive=false 仍返回 200，由客户端看字段；进程都起不来才会连不上
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        try:
            self._httpd = ThreadingHTTPServer((host, port), Handler)
        except Exception as exc:
            self.log.warning("灾备 HTTP health 启动失败: %s", exc)
            return

        def _serve() -> None:
            assert self._httpd is not None
            self._httpd.serve_forever(poll_interval=0.5)

        self._http_thread = threading.Thread(
            target=_serve, daemon=True, name="failover-http"
        )
        self._http_thread.start()
        self.log.warning(
            "灾备 HTTP health: http://%s:%s/health （返回内存最近请求时间）",
            host if host != "0.0.0.0" else "0.0.0.0",
            port,
        )
