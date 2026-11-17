import os
from typing import Any, Dict

from src.config_secrets import load_merged_config


APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_ROOT, "config", "config.json")
SECRETS_PATH = os.path.join(APP_ROOT, "config", "secrets.local.json")


def _load_config() -> Dict[str, Any]:
    # 公开结构 config.json + 本地密钥 secrets.local.json（不入库）
    return load_merged_config(CONFIG_PATH, SECRETS_PATH)


CONFIG = _load_config()

# 调度间隔：优先秒；未配则用分钟*60（默认 30 秒）
if CONFIG.get("schedule_interval_seconds") is not None:
    SCHEDULE_INTERVAL_SECONDS = max(1, int(CONFIG.get("schedule_interval_seconds") or 30))
else:
    SCHEDULE_INTERVAL_SECONDS = max(1, int(float(CONFIG.get("schedule_interval_minutes", 0.5)) * 60))
SCHEDULE_INTERVAL_MINUTES = SCHEDULE_INTERVAL_SECONDS / 60.0
LOG_LEVEL = str(CONFIG.get("log_level", "INFO")).upper()

SOURCES = CONFIG.get("sources") or {}
MERCARI_ENABLED = bool(SOURCES.get("mercari", True))
PAYPAY_ENABLED = bool(SOURCES.get("paypay", True))
YAHOO_AUCTION_ENABLED = bool(SOURCES.get("yahoo_auction", True))

DEEPSEEK = CONFIG.get("deepseek") or {}
WECOM = CONFIG.get("wecom") or {}
EMAIL = CONFIG.get("email") or {}
YAHOO_AUCTION = CONFIG.get("yahoo_auction") or {}
MERCARI = CONFIG.get("mercari") or {}
PAYPAY = CONFIG.get("paypay") or {}
TEST = CONFIG.get("test") or {}
PATHS = CONFIG.get("paths") or {}


def abs_path(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    return os.path.join(APP_ROOT, rel)


SUBSCRIPTIONS_PATH = abs_path(PATHS.get("subscriptions", "config/subscriptions.json"))
REFERENCE_ITEMS_PATH = abs_path(PATHS.get("reference_items", "config/reference_items.md"))
SEEN_STATE_PATH = abs_path(PATHS.get("seen_state", "data/seen_ids.json"))
DPOP_KEY_PATH = abs_path(PATHS.get("dpop_key", "config/dpop_es256_key.pem"))
