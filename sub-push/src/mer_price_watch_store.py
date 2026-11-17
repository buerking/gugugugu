import json
import os
from typing import Any, Dict, List

from src import settings


def load_mer_price_watches() -> List[Dict[str, Any]]:
    path = settings.MER_PRICE_WATCHES_JSON_PATH
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_mer_price_watches(rows: List[Dict[str, Any]]) -> None:
    path = settings.MER_PRICE_WATCHES_JSON_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
