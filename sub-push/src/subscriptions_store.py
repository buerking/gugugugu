import json
import os
import re
from typing import Any, Dict, List

from src import settings


def subscriptions_json_path() -> str:
    cfg = settings.CONFIG.get("subscriptions", {})
    rel = cfg.get("json_path", "config/subscriptions.json")
    return os.path.join(settings.APP_ROOT, rel)


def load_subscriptions() -> List[Dict[str, Any]]:
    path = subscriptions_json_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_subscriptions(subs: List[Dict[str, Any]]) -> None:
    path = subscriptions_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)


def sync_to_python_get_subscription_records(subs: List[Dict[str, Any]]) -> str:
    """
    将订阅数据写回到原项目的 `subscriptions.py`（覆盖其 `get_subscription_records()` 的返回列表）。
    返回写入的文件路径。
    """
    cfg = settings.CONFIG.get("subscriptions", {})
    py_rel = cfg.get("python_module_path", "../subscriptions.py")
    py_path = os.path.abspath(os.path.join(settings.APP_ROOT, py_rel))
    if not os.path.exists(py_path):
        raise FileNotFoundError(py_path)

    with open(py_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 仅替换 get_subscription_records() 中的 test_data/list 内容（粗粒度但可控）
    pattern = re.compile(r"(def\s+get_subscription_records\s*\(\)\s*:[\s\S]*?\n\s*test_data\s*=\s*)\[[\s\S]*?\](\s*\n\s*return\s+test_data)", re.M)
    new_list = json.dumps(subs, ensure_ascii=False, indent=8)
    new_list = "\n".join(" " * 8 + line for line in new_list.splitlines())  # 缩进对齐

    if not pattern.search(content):
        # fallback：替换 return 语句为直接 return [...]
        pattern2 = re.compile(r"(def\s+get_subscription_records\s*\(\)\s*:[\s\S]*?\n)(\s*return\s+.+)", re.M)
        if not pattern2.search(content):
            raise RuntimeError("未找到可替换的 get_subscription_records() 结构")
        replacement = r"\1    return " + json.dumps(subs, ensure_ascii=False, indent=4).replace("\n", "\n    ")
        content = pattern2.sub(replacement, content, count=1)
    else:
        content = pattern.sub(r"\1" + new_list + r"\2", content, count=1)

    with open(py_path, "w", encoding="utf-8") as f:
        f.write(content)

    return py_path

