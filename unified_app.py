"""兼容入口：转到本仓库 sub-push 统一窗口（公共后端 + VIP）。"""

from __future__ import annotations

import os
import runpy
import sys


SUB_APP = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sub-push", "app.py")
)


def main() -> None:
    if not os.path.isfile(SUB_APP):
        raise SystemExit(f"找不到统一窗口: {SUB_APP}")
    sub_root = os.path.dirname(SUB_APP)
    if sub_root not in sys.path:
        sys.path.insert(0, sub_root)
    os.chdir(sub_root)
    runpy.run_path(SUB_APP, run_name="__main__")


if __name__ == "__main__":
    main()
