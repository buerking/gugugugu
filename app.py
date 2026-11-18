"""
仓库根入口：普通订阅 + 大客户 VIP + 物流守护。

用法（在 push 根目录）：
  python app.py
  python app.py --auto-start          # 打开即启动全部（默认）
  python app.py --no-auto-start       # 只开 GUI，手动点「启动全部」
  python app.py --no-logistics        # 不启物流旁路
"""

from __future__ import annotations

import importlib.util
import os
import sys


ROOT = os.path.dirname(os.path.abspath(__file__))
SUB_PUSH = os.path.join(ROOT, "sub-push")
SUB_APP = os.path.join(SUB_PUSH, "app.py")


def _load_subpush_app():
    """按路径加载 sub-push/app.py，避免与本文件同名冲突。"""
    if not os.path.isfile(SUB_APP):
        raise SystemExit(f"找不到统一 GUI: {SUB_APP}")

    os.chdir(SUB_PUSH)
    # 脚本目录常被放进 sys.path[0]；先去掉根目录，再插入 sub-push
    root_norm = os.path.normcase(os.path.abspath(ROOT))
    sys.path[:] = [
        p
        for p in sys.path
        if os.path.normcase(os.path.abspath(p or os.getcwd())) != root_norm
    ]
    if SUB_PUSH not in sys.path:
        sys.path.insert(0, SUB_PUSH)

    spec = importlib.util.spec_from_file_location("subpush_gui_app", SUB_APP)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载: {SUB_APP}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["subpush_gui_app"] = mod
    spec.loader.exec_module(mod)
    return mod.App


def main() -> None:
    argv = sys.argv[1:]
    auto_start = "--no-auto-start" not in argv
    with_logistics = "--no-logistics" not in argv
    if "--auto-start" in argv or "--start" in argv:
        auto_start = True

    App = _load_subpush_app()
    App(auto_start=auto_start, with_logistics=with_logistics).mainloop()


if __name__ == "__main__":
    main()
