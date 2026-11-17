from typing import Any, Dict, List

from src.subscriptions_store import load_subscriptions as load_from_store


def load_subscriptions() -> List[Dict[str, Any]]:
    # GUI 会维护 config/subscriptions.json，这里统一从该 store 读取
    return load_from_store()

