"""单独用 Edge 打开 edi 后台并自动登录（不碰 Chrome，打开后保持窗口）。"""

from __future__ import annotations

import logging
import os
import sys
import time

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from src.logistics.monitor import (  # noqa: E402
    ADMIN_LIST_URL,
    _maybe_login,
    _wait_order_table,
    build_edge,
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    url = ADMIN_LIST_URL
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        url = sys.argv[1]

    edge = build_edge()
    ua = edge.evaluate("navigator.userAgent")
    print(f"port={edge.port}")
    print(f"UA={ua}")
    if "Edg/" not in str(ua):
        raise SystemExit("不是 Edge，已中止")

    edge.navigate(url, wait_seconds=4)
    _maybe_login(edge)
    # 登录后若仍停在 login，再打开一次目标页
    if "login" in (edge.current_url() or "").lower():
        time.sleep(1)
        edge.navigate(url, wait_seconds=3)
        _maybe_login(edge)

    try:
        _wait_order_table(edge, timeout=25)
        print("订单表格已出现")
    except Exception as e:
        print(f"等待表格: {e}")

    print(f"url={edge.current_url()}")
    print(f"title={edge.evaluate('document.title')}")
    print(
        "has_table=",
        edge.evaluate("!!document.querySelector('table.public_table')"),
    )
    # 保持浏览器：断开 CDP，但不 taskkill Edge
    try:
        if edge._ws is not None:
            edge._ws.close()
    except Exception:
        pass
    edge._ws = None
    edge._proc = None
    print("Edge 已打开 edi，窗口保持；本脚本结束（未关闭浏览器，未动 Chrome）。")


if __name__ == "__main__":
    main()
