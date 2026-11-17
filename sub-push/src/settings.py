import os
from typing import Any, Dict, List

from src.config_secrets import load_merged_config, save_split_config


# sub-push/src/settings.py -> sub-push
APP_ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(APP_ROOT, "config", "config.json")
SECRETS_PATH = os.path.join(APP_ROOT, "config", "secrets.local.json")


def _load_config() -> Dict[str, Any]:
    # 公开结构 config.json + 本地密钥 secrets.local.json（不入库）
    return load_merged_config(CONFIG_PATH, SECRETS_PATH)


def save_config(cfg: Dict[str, Any]) -> None:
    # 密钥写回 secrets.local.json，避免落入可提交的 config.json
    save_split_config(cfg, CONFIG_PATH, SECRETS_PATH)


CONFIG: Dict[str, Any] = _load_config()

LOG_LEVEL = CONFIG["app"]["log_level"]
# 测试期 GUI 控制台透传 INFO；上线可改 false 只留 WARNING+标记日志
GUI_VERBOSE = bool(CONFIG["app"].get("gui_verbose", True))
SCHEDULE_INTERVAL_MINUTES = int(CONFIG["app"]["schedule_interval_minutes"])
SEARCH_CONCURRENCY = int(CONFIG["app"].get("search_concurrency") or 5)

_pool = CONFIG.get("search_pool") or {}
# 兼容旧字段 search_concurrency 作为 max_workers 上限
_default_max = int(_pool.get("max_workers") or SEARCH_CONCURRENCY or 8)
SEARCH_POOL: Dict[str, Any] = {
    "per_worker_capacity": max(1, int(_pool.get("per_worker_capacity") or 5)),
    "min_workers": max(1, int(_pool.get("min_workers") or 1)),
    "max_workers": max(1, min(16, _default_max)),
    "serial_below_or_equal": max(0, int(_pool.get("serial_below_or_equal") or 5)),
}

# AI 仅 big 使用；公共 sub-push 强制关闭（忽略配置里的 enabled）
AI_ENABLED = False

_mem = CONFIG.get("memory") or {}
MEMORY: Dict[str, Any] = {
    "gc_enabled": bool(_mem.get("gc_enabled", True)),
    "gc_every_workers": max(1, int(_mem.get("gc_every_workers") or 1)),
    "gc_every_jobs": max(1, int(_mem.get("gc_every_jobs") or 25)),
    "title_max_chars": max(40, int(_mem.get("title_max_chars") or 200)),
}

_tw = CONFIG.get("time_window") or {}
TIME_WINDOW: Dict[str, Any] = {
    "watermark_slack_seconds": int(_tw.get("watermark_slack_seconds") or 60),
    "no_watermark_factor": float(_tw.get("no_watermark_factor") or 1.5),
    "max_lookback_hours": float(_tw.get("max_lookback_hours") or 24),
    "drop_items_without_time": bool(_tw.get("drop_items_without_time") or False),
}

RESULT_DIR = os.path.join(APP_ROOT, CONFIG["storage"]["result_dir"])
ARCHIVE_DIR = os.path.join(RESULT_DIR, CONFIG["storage"]["archive_dir_name"])

_seen = CONFIG.get("seen") or {}
SEEN_DB_PATH = os.path.join(APP_ROOT, _seen.get("sqlite_path") or "data/seen_items.sqlite3")

# —— 搜索源（big 同款直连）——
_mercari = CONFIG.get("sources", {}).get("mercari") or {}
MERCARI: Dict[str, Any] = dict(_mercari)
MERCARI_ENABLED = bool(_mercari.get("enabled", True))
# 兼容旧字段（降价监控等仍可能引用）
MERCARI_BASE_URL = str(_mercari.get("base_url") or "")
MERCARI_SECRET_KEY = str(_mercari.get("secret_key") or "")

_paypay = CONFIG.get("sources", {}).get("paypay") or {}
PAYPAY: Dict[str, Any] = dict(_paypay)
PAYPAY_ENABLED = bool(_paypay.get("enabled", True))
PAYPAY_SEARCH_URL = str(_paypay.get("search_url") or "https://paypayfleamarket.yahoo.co.jp/api/v1/search")
PAYPAY_ITEM_URL_TEMPLATE = str(
    _paypay.get("item_url_template")
    or "https://paypayfleamarket.yahoo.co.jp/item/{item_id}"
)
PAYPAY_DEFAULT_PARAMS = _paypay.get("default_params") or {}

_yahoo = CONFIG.get("sources", {}).get("yahoo_auction") or {}
YAHOO_AUCTION: Dict[str, Any] = dict(_yahoo)
YAHOO_AUCTION_ENABLED = bool(_yahoo.get("enabled", False))

DEFAULT_SEARCH_SOURCES: List[str] = []
if MERCARI_ENABLED:
    DEFAULT_SEARCH_SOURCES.append("mercari")
if PAYPAY_ENABLED:
    DEFAULT_SEARCH_SOURCES.append("paypay")
if YAHOO_AUCTION_ENABLED:
    DEFAULT_SEARCH_SOURCES.append("yahoo_auction")
if not DEFAULT_SEARCH_SOURCES:
    DEFAULT_SEARCH_SOURCES = ["mercari", "paypay"]

DPOP_KEY_PATH = os.path.join(
    APP_ROOT,
    str((CONFIG.get("paths") or {}).get("dpop_key") or "config/dpop_es256_key.pem"),
)

PURCHASE_PREFIX = CONFIG["purchase"]["prefix"]

SUBSCRIPTIONS_JSON_PATH = os.path.join(APP_ROOT, CONFIG["subscriptions"]["json_path"])
SELLER_SUBSCRIPTIONS_JSON_PATH = os.path.join(APP_ROOT, CONFIG["seller_subscriptions"]["json_path"])

_ktf = CONFIG.get("keyword_title_filter", {})
KEYWORD_TITLE_FILTER_ENABLED = bool(_ktf.get("enabled", True))
KEYWORD_TITLE_FILTER_MIN_OTHER_RATIO = float(_ktf.get("min_other_token_match_ratio", 0.6))
KEYWORD_TITLE_FILTER_STOPLIST = _ktf.get("stoplist_extra", []) or []

_mpw = CONFIG.get("mer_price_watches", {})
MER_PRICE_WATCHES_JSON_PATH = os.path.join(APP_ROOT, _mpw.get("json_path", "config/mer_price_watches.json"))
MER_PRICE_WATCH_STATE_PATH = os.path.join(APP_ROOT, _mpw.get("state_path", "config/mer_price_watch_state.json"))
MER_PRICE_WATCH_BATCH_SIZE = int(_mpw.get("batch_size", 50))
MER_PRICE_WATCH_JPKIX_APP_ID = str(_mpw.get("jpkix_app_id", "purchase_good_info"))

_ftp = CONFIG.get("ftp") or {}
FTP_HOST = _ftp.get("host", "")
FTP_PORT = int(_ftp.get("port") or 21)
FTP_USER = _ftp.get("user", "")
FTP_PASS = _ftp.get("password", "")
FTP_WEB_PREFIX = _ftp.get("web_prefix", "")

BACKEND_PUSH: Dict[str, Any] = dict(CONFIG.get("backend_push") or {})

_feishu = CONFIG.get("feishu") or {}
FEISHU: Dict[str, Any] = {
    "enabled": bool(_feishu.get("enabled", True)),
    "webhook_url": str(
        _feishu.get("webhook_url")
        or ""
    ),
    "title_prefix": str(_feishu.get("title_prefix") or "[sub-push]"),
    "cooldown_seconds": float(_feishu.get("cooldown_seconds") or 300),
    "timeout_seconds": float(_feishu.get("timeout_seconds") or 10),
}

_wd = CONFIG.get("watchdog") or {}
WATCHDOG: Dict[str, Any] = {
    "max_retries_per_round": max(0, int(_wd.get("max_retries_per_round") or 2)),
    "retry_delay_seconds": float(_wd.get("retry_delay_seconds") or 15),
    "fail_notify_after": max(1, int(_wd.get("fail_notify_after") or 1)),
    "stale_alert_seconds": float(_wd.get("stale_alert_seconds") or 1800),
    "heartbeat_check_seconds": float(_wd.get("heartbeat_check_seconds") or 60),
    "alive_notify_seconds": float(_wd.get("alive_notify_seconds") or 0),
    "notify_on_start": bool(_wd.get("notify_on_start", True)),
    "notify_on_stop": bool(_wd.get("notify_on_stop", False)),
}

# 上新订阅正式 API（jpkix data+sign）；enabled 后优先生效
_sub_api = CONFIG.get("subscription_api") or {}
SUBSCRIPTION_API: Dict[str, Any] = {
    # 正式版固定走线上接口，无本地 JSON 兜底
    "enabled": True,
    "dry_run": bool(_sub_api.get("dry_run", False)),
    "base_url": str(
        _sub_api.get("base_url")
        or "https://p.jpkix.com/yii/web/index.php"
    ),
    "route_prefix": str(_sub_api.get("route_prefix") or "home/api"),
    "sign_key": str(_sub_api.get("sign_key") or "purchaseApi"),
    "get_action": str(_sub_api.get("get_action") or "get-search-subscriptions"),
    "push_action": str(
        _sub_api.get("push_action") or "push-search-subscription-hits"
    ),
    "timeout_seconds": float(_sub_api.get("timeout_seconds") or 30),
    "max_pages": int(_sub_api.get("max_pages") or 100),
    "max_groups_per_request": int(_sub_api.get("max_groups_per_request") or 1000),
    "max_items_per_request": int(_sub_api.get("max_items_per_request") or 5000),
}

# 大客户 VIP：与公共订阅共用搜索/seen，冷却期走 AI+邮件（复用 big 工程）
_vip = CONFIG.get("vip") or {}
# 生产整合后默认：push/sub-push 与 push/big 并列
_default_big = os.path.normpath(os.path.join(APP_ROOT, "..", "big"))
_cfg_big = str(_vip.get("big_root") or "").strip()
if _cfg_big:
    _big_root = (
        _cfg_big
        if os.path.isabs(_cfg_big)
        else os.path.normpath(os.path.join(APP_ROOT, _cfg_big))
    )
else:
    _big_root = _default_big
VIP: Dict[str, Any] = {
    "enabled": bool(_vip.get("enabled", True)),
    "big_root": _big_root,
    "subscriptions_path": str(_vip.get("subscriptions_path") or ""),
    "ai_max_items_per_user": int(_vip.get("ai_max_items_per_user") or 40),
    "loop_idle_seconds": float(_vip.get("loop_idle_seconds") or 5),
}

_middle = CONFIG.get("middle_layer") or {}
MIDDLE_LAYER: Dict[str, Any] = {
    "enabled": bool(_middle.get("enabled", True)),
    "slice_size": max(1, int(_middle.get("slice_size") or 80)),
    "ttl_days": float(_middle.get("ttl_days") if _middle.get("ttl_days") is not None else 7),
    "persist_path": str(
        _middle.get("persist_path") or "data/subscription_middle.json"
    ),
    # 大客户与公共混编进同一中间层，均匀散片（雅虎闲置不单独串行）
    "mix_vip": bool(_middle.get("mix_vip", True)),
}

# 站外 HTTP：本机用 curl(Schannel)；单次超时默认 10s，超时放弃
_http = CONFIG.get("http") or {}
HTTP: Dict[str, Any] = {
    "transport": str(_http.get("transport") or "curl"),
    "connect_timeout": int(
        _http["connect_timeout"] if "connect_timeout" in _http else 5
    ),
    "max_time": int(_http["max_time"] if "max_time" in _http else 10),
    "retries": int(_http["retries"] if "retries" in _http else 0),
}
