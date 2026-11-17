"""通过 CDP 控制本机 Edge（msedge.exe），严禁连到 Chrome。

要点：
1. 调试口默认 9333，避开 Chrome 常用的 9222
2. /json/version 必须识别为 Edge，否则直接报错
3. 使用独立 user-data-dir（默认配置无法可靠开 remote-debugging）
4. 启动前从本机 Edge Default 同步 Cookies，尽量复用已登录态
5. taskkill 只杀 msedge.exe，绝不碰 chrome.exe
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.request import urlopen

import websocket


logger = logging.getLogger(__name__)

EDGE_BINARY = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
# 避开 Chrome 常用的 9222
DEFAULT_DEBUG_PORT = 9333


def default_logistics_user_data() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "Edge",
        "User Data LogisticsCDP",
    )


def main_edge_user_data() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "Edge",
        "User Data",
    )


def _is_edge_browser(version: Dict[str, Any]) -> bool:
    browser = str(version.get("Browser") or "")
    ua = str(version.get("User-Agent") or "")
    product = str(version.get("Product") or "")
    blob = f"{browser} {ua} {product}".lower()
    if "chrome/" in browser.lower() and "edg" not in browser.lower():
        return False
    if "edg/" in blob or "microsoft edge" in blob or "msedge" in blob:
        return True
    if browser.lower().startswith("edg"):
        return True
    return False


def _kill_edge_only() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "msedge.exe", "/T"],
        capture_output=True,
        check=False,
    )
    time.sleep(1.5)


def sync_edge_login_files(
    *,
    src_user_data: Optional[str] = None,
    dst_user_data: Optional[str] = None,
    profile: str = "Default",
) -> None:
    """把主 Edge 配置里的登录相关文件拷到自动化专用目录（Edge 须已关闭）。"""
    src_root = src_user_data or main_edge_user_data()
    dst_root = dst_user_data or default_logistics_user_data()
    src = os.path.join(src_root, profile)
    dst = os.path.join(dst_root, profile)
    if not os.path.isdir(src):
        logger.warning("主 Edge 配置不存在，跳过 Cookie 同步: %s", src)
        return
    os.makedirs(dst, exist_ok=True)
    os.makedirs(os.path.join(dst, "Network"), exist_ok=True)

    pairs = [
        ("Cookies", "Cookies"),
        ("Login Data", "Login Data"),
        ("Login Data For Account", "Login Data For Account"),
        (os.path.join("Network", "Cookies"), os.path.join("Network", "Cookies")),
        (os.path.join("Network", "Cookies-journal"), os.path.join("Network", "Cookies-journal")),
        ("Preferences", "Preferences"),
    ]
    for rel_src, rel_dst in pairs:
        s = os.path.join(src, rel_src)
        d = os.path.join(dst, rel_dst)
        if not os.path.exists(s):
            continue
        try:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            logger.info("已同步 Edge 登录文件: %s", rel_src)
        except Exception as e:
            logger.warning("同步失败 %s: %s", rel_src, e)


class EdgeCDP:
    def __init__(
        self,
        *,
        user_data_dir: str,
        profile_directory: str = "Default",
        port: int = DEFAULT_DEBUG_PORT,
        edge_binary: str = EDGE_BINARY,
        sync_login: bool = True,
    ) -> None:
        self.user_data_dir = user_data_dir
        self.profile_directory = profile_directory
        self.port = int(port)
        self.edge_binary = edge_binary
        self.sync_login = sync_login
        self._proc: Optional[subprocess.Popen] = None
        self._ws: Optional[websocket.WebSocket] = None
        self._msg_id = 0
        self._target_id: Optional[str] = None
        self._browser_info: Dict[str, Any] = {}

    def start(self) -> None:
        if not os.path.exists(self.edge_binary):
            raise FileNotFoundError(f"找不到 Edge: {self.edge_binary}")

        self.port = self._pick_free_edge_port(self.port)

        # 只关 Edge，绝不碰 chrome.exe
        _kill_edge_only()

        if self.sync_login:
            sync_edge_login_files(
                src_user_data=main_edge_user_data(),
                dst_user_data=self.user_data_dir,
                profile=self.profile_directory,
            )

        os.makedirs(self.user_data_dir, exist_ok=True)
        args = [
            self.edge_binary,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.user_data_dir}",
            f"--profile-directory={self.profile_directory}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "about:blank",
        ]
        logger.info(
            "启动 Edge: binary=%s port=%s profile=%s user_data=%s",
            self.edge_binary,
            self.port,
            self.profile_directory,
            self.user_data_dir,
        )
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready_edge()
        self._attach_page()

        ua = str(self.evaluate("navigator.userAgent") or "")
        if "Edg/" not in ua:
            self.close()
            raise RuntimeError(f"附着后 UA 不是 Edge（可能误连 Chrome）: {ua[:160]}")
        logger.info("已确认 Edge UA: %s", ua[:140])

    def _fetch_version(self, port: int) -> Optional[Dict[str, Any]]:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            return None

    def _pick_free_edge_port(self, preferred: int) -> int:
        candidates = [preferred, 9333, 9334, 9335, 19222, 19223]
        seen = set()
        for port in candidates:
            if port in seen:
                continue
            seen.add(port)
            ver = self._fetch_version(port)
            if ver is None:
                return port
            if _is_edge_browser(ver):
                logger.warning("端口 %s 已是 Edge，将重启 Edge 占用", port)
                return port
            logger.warning(
                "端口 %s 已被非 Edge 占用 Browser=%s，跳过（不碰 Chrome）",
                port,
                ver.get("Browser"),
            )
        raise RuntimeError("找不到可用的 Edge 调试端口（9222 等可能被 Chrome 占用）")

    def _wait_ready_edge(self, timeout: float = 35.0) -> None:
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                ver = self._fetch_version(self.port)
                if not ver:
                    time.sleep(0.4)
                    continue
                if not _is_edge_browser(ver):
                    raise RuntimeError(
                        f"端口 {self.port} 连到的不是 Edge: Browser={ver.get('Browser')!r}。"
                        f"禁止使用 Chrome 的 9222。"
                    )
                if ver.get("webSocketDebuggerUrl"):
                    self._browser_info = ver
                    logger.info("CDP 已就绪且确认为 Edge: Browser=%s", ver.get("Browser"))
                    return
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(0.4)
        raise RuntimeError(f"Edge CDP 未就绪: {last_err}")

    def _list_targets(self) -> List[Dict[str, Any]]:
        with urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _attach_page(self) -> None:
        pages = [t for t in self._list_targets() if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("没有可用的 Edge page target")
        target = None
        for t in pages:
            u = str(t.get("url") or "")
            if u.startswith("about:") or "newtab" in u.lower():
                target = t
                break
        if target is None:
            target = pages[0]
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("page 缺少 webSocketDebuggerUrl")
        self._target_id = target.get("id")
        self._ws = websocket.create_connection(ws_url, timeout=30)
        self._ws.settimeout(60)
        self.call("Page.enable")
        self.call("Runtime.enable")
        logger.info("已附着 Edge 标签 id=%s url=%s", self._target_id, target.get("url"))

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60.0) -> Any:
        if self._ws is None:
            raise RuntimeError("CDP 未连接")
        self._msg_id += 1
        msg_id = self._msg_id
        payload = {"id": msg_id, "method": method, "params": params or {}}
        self._ws.send(json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._ws.recv()
            data = json.loads(raw)
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(f"CDP {method} 失败: {data['error']}")
            return data.get("result")
        raise TimeoutError(f"CDP 超时: {method}")

    def navigate(self, url: str, wait_seconds: float = 3.0) -> None:
        self.call("Page.navigate", {"url": url})
        time.sleep(wait_seconds)
        try:
            self.call(
                "Runtime.evaluate",
                {"expression": "document.readyState", "returnByValue": True},
            )
        except Exception:
            pass

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if not result:
            return None
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return (result.get("result") or {}).get("value")

    def page_source(self) -> str:
        html = self.evaluate("document.documentElement.outerHTML")
        return html or ""

    def body_text(self) -> str:
        text = self.evaluate("document.body ? document.body.innerText : ''")
        return text or ""

    def click_selector(self, selector: str) -> bool:
        js = f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return false;
  el.click();
  return true;
}})()
"""
        return bool(self.evaluate(js))

    def fill_selector(self, selector: str, value: str) -> bool:
        """填写 input/textarea（先 focus，再设 value 并触发 input/change）。"""
        js = f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return false;
  el.focus();
  el.value = {json.dumps(value)};
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return true;
}})()
"""
        return bool(self.evaluate(js))

    def current_url(self) -> str:
        return str(self.evaluate("location.href") or "")

    def close(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        _kill_edge_only()
