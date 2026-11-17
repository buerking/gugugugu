import json
import os
from typing import Any, Dict, List

from src import settings


def load_seller_subscriptions() -> List[Dict[str, Any]]:
    path = settings.SELLER_SUBSCRIPTIONS_JSON_PATH
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_seller_subscriptions(subs: List[Dict[str, Any]]) -> None:
    path = settings.SELLER_SUBSCRIPTIONS_JSON_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)

