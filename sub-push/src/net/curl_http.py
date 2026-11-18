"""
统一 HTTP：curl(Schannel)，正文落临时文件；单次超时严格按配置（默认 10s）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from src import settings


logger = logging.getLogger(__name__)


def _cfg() -> Dict[str, Any]:
    return dict(getattr(settings, "HTTP", None) or {})


def _timeouts() -> Tuple[int, int, int]:
    cfg = _cfg()
    # 严格尊重配置，不再用 connect+5 抬高 max_time
    connect = max(1, int(cfg["connect_timeout"] if "connect_timeout" in cfg else 5))
    max_time = max(1, int(cfg["max_time"] if "max_time" in cfg else 10))
    if max_time < connect:
        max_time = connect
    retries = max(0, int(cfg["retries"] if "retries" in cfg else 0))
    return connect, max_time, retries


def curl_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    compressed: bool = True,
) -> Tuple[int, str]:
    connect, max_time, retries = _timeouts()
    final_url = url
    if params:
        q = urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        if q:
            final_url = f"{url}{'&' if '?' in url else '?'}{q}"

    try:
        from src.failover.runtime_pulse import note_request

        note_request("curl", f"{method.upper()} {final_url[:200]}")
    except Exception:
        pass

    fd, body_path = tempfile.mkstemp(prefix="subpush_curl_", suffix=".bin")
    os.close(fd)
    try:
        cmd: List[str] = [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--connect-timeout",
            str(connect),
            "--max-time",
            str(max_time),
            "-o",
            body_path,
            "-w",
            "%{http_code}",
            "-X",
            method.upper(),
            "--url",
            final_url,
        ]
        if compressed:
            cmd.insert(4, "--compressed")
        if retries > 0:
            cmd.extend(
                [
                    "--retry",
                    str(retries),
                    "--retry-delay",
                    "1",
                    "--retry-all-errors",
                ]
            )
        for k, v in (headers or {}).items():
            if v is None:
                continue
            cmd.extend(["-H", f"{k}: {v}"])
        if body is not None:
            cmd.extend(["--data-binary", body])

        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"curl failed: {err or result.returncode}")

        code_s = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        try:
            http_code = int(code_s or "0")
        except Exception:
            http_code = 0

        with open(body_path, "rb") as f:
            raw = f.read()
        return http_code, raw.decode("utf-8", errors="replace")
    finally:
        try:
            os.remove(body_path)
        except Exception:
            pass


def curl_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    code, text = curl_request(
        method, url, headers=headers, body=body, params=params, compressed=True
    )
    if code >= 400:
        raise RuntimeError(f"HTTP {code}: {(text or '')[:300]}")
    if not (text or "").strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # 不二次请求（会把耗时翻倍）；直接失败交给上层超时/跳过
        raise RuntimeError(
            f"非 JSON 响应 HTTP {code}: {exc}; "
            f"len={len(text or '')} tail={(text or '')[-80:]}"
        ) from exc
    if not isinstance(data, dict):
        return {"_data": data}
    return data
