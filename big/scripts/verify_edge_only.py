"""验证物流模块只连 Edge，不会连上 Chrome(9222)。"""

from __future__ import annotations

import json
import logging
import os
import sys
from urllib.request import urlopen

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from src.logistics.edge_cdp import EdgeCDP, EDGE_BINARY, DEFAULT_DEBUG_PORT  # noqa: E402
from src.logistics.monitor import _edge_user_data, _edge_profile, build_edge  # noqa: E402


def sniff_ports() -> None:
    print("当前本机调试口探测：")
    for port in (9222, 9333, 9334, 19222):
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                d = json.loads(r.read().decode())
            print(f"  {port}: Browser={d.get('Browser')!r}")
        except Exception:
            print(f"  {port}: (空闲/不可达)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"默认 Edge 端口配置: {DEFAULT_DEBUG_PORT}（避开 Chrome 9222）")
    print(f"Edge 二进制: {EDGE_BINARY} exists={os.path.exists(EDGE_BINARY)}")
    sniff_ports()

    print("\n启动 build_edge() …")
    edge = build_edge()
    try:
        ua = edge.evaluate("navigator.userAgent")
        url = edge.current_url()
        print(f"附着成功 UA={ua}")
        print(f"当前 URL={url}")
        print(f"使用端口={edge.port}")
        if "Edg/" not in str(ua):
            raise SystemExit("失败：UA 无 Edg/，不是 Edge")
        if edge.port == 9222:
            raise SystemExit("失败：仍在使用 9222（Chrome 常用口）")
        print("校验通过：确认为 Edge，且未使用 Chrome 的 9222。")
    finally:
        edge.close()
        print("已关闭 Edge。")


if __name__ == "__main__":
    main()
