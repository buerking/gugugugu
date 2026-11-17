from typing import Any, Dict

from src import settings


def normalize_subscription(subscription: Dict[str, Any]) -> Dict[str, Any]:
    # 固定订阅模型，所有站点都从这个模型取值
    return {
        "keyword": subscription.get("keyword", ""),
        "category_id": str(subscription.get("category_id", "")),
        "sub_user_id": str(subscription.get("sub_user_id", "")),
        "user_email": str(subscription.get("user_email", "")),
        "user_phone": str(subscription.get("user_phone", "")),
        "created_time": int(subscription.get("created_time", 0) or 0),
        "brand_id": str(subscription.get("brand_id", "")),
        "price_min": subscription.get("price_min", ""),
        "price_max": subscription.get("price_max", "")
    }


def map_subscription_to_source_request(source: str, subscription: Dict[str, Any], created_after_date: str = "") -> Dict[str, Any]:
    """
    通用映射器：
    固定订阅字段 -> 站点接口参数字段
    """
    mapping_cfg = settings.CONFIG["field_mapping"]["source_request_mapping"].get(source, {})
    normalized = normalize_subscription(subscription)
    out: Dict[str, Any] = {}
    for target_key, from_key in mapping_cfg.items():
        if from_key == "created_after_date":
            out[target_key] = created_after_date
        else:
            out[target_key] = normalized.get(from_key, "")
    return out

