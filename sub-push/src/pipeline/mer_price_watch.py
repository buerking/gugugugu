"""
煤炉商品降价监控：通过 jpkix fetch 批量拉取商品详情，与本地快照对比，仅在降价时发信。
逻辑参考项目根目录 mer_update.py。
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

from src import settings
from src.mer_price_watch_store import load_mer_price_watches
from src.sources.mercari_client import create_sign

try:
    from notifier import Notifier
except Exception:  # pragma: no cover
    class Notifier:  # type: ignore
        @staticmethod
        def update_send_mail(user_id: str, item_id: str, msg: str) -> None:
            logging.getLogger(__name__).warning(
                "Notifier.update_send_mail 不可用: user=%s item=%s", user_id, item_id
            )


logger = logging.getLogger(__name__)


def _normalize_item_id(raw: str) -> str:
    s = str(raw).strip()
    if not s:
        return ""
    if s.lower().startswith("m"):
        return "m" + s[1:].strip()
    if s.isdigit():
        return "m" + s
    return s


def _parse_items_field(items: Any) -> List[str]:
    if items is None:
        return []
    if isinstance(items, str):
        parts = items.replace(",", " ").replace("\n", " ").split()
        return [_normalize_item_id(p) for p in parts if _normalize_item_id(p)]
    if isinstance(items, list):
        out: List[str] = []
        for x in items:
            nid = _normalize_item_id(x)
            if nid:
                out.append(nid)
        return out
    return []


def _load_state() -> Dict[str, Dict[str, Any]]:
    path = settings.MER_PRICE_WATCH_STATE_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("读取降价监控状态失败，将重建: %s", exc)
        return {}


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    path = settings.MER_PRICE_WATCH_STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "price": item.get("price"),
        "updated": item.get("updated"),
        "status": item.get("status"),
    }


def _fetch_batch(item_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """返回 id -> 商品字典（与 mer_update 一致，Data.items 为对象）。"""
    if not item_ids:
        return {}
    code_json = quote_plus(json.dumps(item_ids))
    app_id = settings.MER_PRICE_WATCH_JPKIX_APP_ID
    params = {"app_id": app_id, "codeArr": code_json}
    sign = create_sign(params, settings.MERCARI_SECRET_KEY)
    url = (
        f"{settings.MERCARI_BASE_URL}"
        f"?r=api/request-transfer/fetch&codeJson={code_json}&sign={sign}&appId={app_id}"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("Success"):
            logger.warning("jpkix fetch 失败: %s", data.get("Message"))
            return {}
        items_obj = data.get("Data", {}).get("items", {})
        if isinstance(items_obj, dict):
            return {str(k): v for k, v in items_obj.items() if isinstance(v, dict)}
        return {}
    except Exception as exc:
        logger.error("jpkix fetch 请求异常: %s", exc)
        return {}


def run_mer_price_watches(first_run: bool = False) -> None:
    if not settings.MERCARI_ENABLED:
        logger.info("降价监控: Mercari(jpkix) 已在 config 中禁用，跳过")
        return

    watches = load_mer_price_watches()
    if not watches:
        logger.info("降价监控: mer_price_watches.json 无配置，跳过")
        return

    logger.info(
        "降价监控: 开始执行，配置条数=%s，first_run=%s，状态文件=%s",
        len(watches),
        first_run,
        settings.MER_PRICE_WATCH_STATE_PATH,
    )

    state = _load_state()

    for watch in watches:
        user_id = str(watch.get("sub_user_id", "")).strip()
        if not user_id:
            logger.warning("降价监控: 跳过一条配置（缺少 sub_user_id）")
            continue
        item_ids = _parse_items_field(watch.get("items"))
        # 去重保序
        seen = set()
        uniq_ids: List[str] = []
        for iid in item_ids:
            if iid not in seen:
                seen.add(iid)
                uniq_ids.append(iid)
        if not uniq_ids:
            logger.info("降价监控: 用户 %s 无有效商品 id，跳过", user_id)
            continue

        if user_id not in state:
            state[user_id] = {}

        logger.info("降价监控: 用户 %s 待监控商品数=%s", user_id, len(uniq_ids))

        batch_size = max(1, settings.MER_PRICE_WATCH_BATCH_SIZE)
        for i in range(0, len(uniq_ids), batch_size):
            batch = uniq_ids[i : i + batch_size]
            fresh_map = _fetch_batch(batch)
            got = len(fresh_map)
            logger.info(
                "降价监控: 用户 %s 批次 %s~%s 请求 id 数=%s，接口返回商品数=%s",
                user_id,
                i,
                min(i + batch_size, len(uniq_ids)) - 1,
                len(batch),
                got,
            )
            for iid in batch:
                item = fresh_map.get(iid)
                if not item:
                    # 常见原因：id 错误、已删除、或接口键名与请求 id 不一致
                    alt = fresh_map.get(iid.lstrip("m")) or fresh_map.get(iid[1:] if iid.startswith("m") else iid)
                    if alt:
                        item = alt
                        logger.warning(
                            "降价监控: 用户 %s 商品 %s 以变体 id 命中返回（请核对 id 格式）",
                            user_id,
                            iid,
                        )
                    else:
                        logger.warning(
                            "降价监控: 用户 %s 商品 %s 在 fetch 结果中缺失，跳过（请检查 id 或接口是否返回）",
                            user_id,
                            iid,
                        )
                        continue

                prev: Optional[Dict[str, Any]] = state[user_id].get(iid)

                if first_run or prev is None:
                    snap = _snapshot(item)
                    state[user_id][iid] = snap
                    logger.info(
                        "降价监控: 用户 %s 商品 %s 首次建档，基准 price=%s updated=%s status=%s（本轮不发降价信）",
                        user_id,
                        iid,
                        snap.get("price"),
                        snap.get("updated"),
                        snap.get("status"),
                    )
                    continue

                if item.get("status") == "sold_out":
                    state[user_id][iid] = _snapshot(item)
                    logger.info("降价监控: 用户 %s 商品 %s 已下架，已更新快照", user_id, iid)
                    continue

                if item.get("updated") == prev.get("updated"):
                    logger.debug(
                        "降价监控: 用户 %s 商品 %s updated 未变，跳过对比",
                        user_id,
                        iid,
                    )
                    continue

                old_price = prev.get("price")
                new_price = item.get("price")
                if old_price is not None and new_price is not None and new_price < old_price:
                    delta = int(new_price) - int(old_price)
                    pct = round(delta / int(old_price) * 100, 2) if old_price else 0.0
                    msg = (
                        f"商品 {iid} | 价格下降: {old_price} → {new_price} "
                        f"(Δ{delta} / {pct}%)"
                    )
                    logger.info("降价监控: 检测到降价，准备通知 | %s", msg)
                    try:
                        Notifier.update_send_mail(user_id, iid, msg)
                    except Exception as exc:
                        logger.error("降价通知发送失败 user=%s item=%s: %s", user_id, iid, exc)
                elif old_price is not None and new_price is not None:
                    logger.info(
                        "降价监控: 用户 %s 商品 %s 有更新但未降价（%s → %s），已刷新快照",
                        user_id,
                        iid,
                        old_price,
                        new_price,
                    )

                state[user_id][iid] = _snapshot(item)

    _save_state(state)
    logger.info("降价监控: 本轮结束，状态已写入")
