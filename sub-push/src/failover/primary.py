"""生产端：对外提供 /health（内存最近请求时间），供开发端按 IP 探测。"""

from __future__ import annotations

import json
import logging
import threading
import traceback
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
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    def _start_http(self, cfg: Dict[str, Any]) -> None:
        http_cfg = dict(cfg.get("http_health") or {})
        if "enabled" in http_cfg and not http_cfg.get("enabled"):
            self.log.warning("failover.http_health.enabled=false，未启动 /health")
            return
        host = str(http_cfg.get("host") or "0.0.0.0")
        port = int(http_cfg.get("port") or 18765)
        node = str(cfg.get("node_id") or default_node_id())
        stale = float(cfg.get("stale_seconds") or 180)
        role = str(cfg.get("role") or "primary")
        log = self.log

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self):  # noqa: N802
                try:
                    path = (self.path or "/").split("?")[0]
                    if path not in ("/health", "/"):
                        body = b'{"ok":false,"error":"not_found"}'
                        self._write(404, body)
                        return
                    body_obj = health_payload(
                        node_id=node, role=role, stale_seconds=stale
                    )
                    raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
                    self._write(200, raw)
                except Exception as exc:
                    log.warning("health handler error: %s\n%s", exc, traceback.format_exc())
                    err = json.dumps(
                        {"ok": False, "error": str(exc)}, ensure_ascii=False
                    ).encode("utf-8")
                    try:
                        self._write(500, err)
                    except Exception:
                        pass

            def _write(self, code: int, raw: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                try:
                    self.wfile.flush()
                except Exception:
                    pass

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        try:
            self._httpd = ThreadingHTTPServer((host, port), Handler)
            # 避免 TIME_WAIT 占端口导致重启失败
            self._httpd.allow_reuse_address = True
        except Exception as exc:
            self.log.warning("灾备 HTTP health 启动失败: %s", exc)
            return

        def _serve() -> None:
            assert self._httpd is not None
            try:
                self._httpd.serve_forever(poll_interval=0.5)
            except Exception as exc:
                log.warning("health serve_forever 退出: %s", exc)

        self._http_thread = threading.Thread(
            target=_serve, daemon=True, name="failover-http"
        )
        self._http_thread.start()
        self.log.warning(
            "灾备 HTTP health: http://%s:%s/health （返回内存最近请求时间）",
            host if host != "0.0.0.0" else "0.0.0.0",
            port,
        )
