"""推送前整理：标题译成简体中文，并按日元兑人民币汇率附上人民币价格。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from src import settings


logger = logging.getLogger(__name__)

_fx_cache: Dict[str, Any] = {"rate": None, "date": "", "label": ""}
_FX_CACHE_PATH = settings.abs_path("data/fx_jpy_cny.json")


def display_title(item: Dict[str, Any]) -> str:
    return str(item.get("name_zh") or item.get("name") or "").strip()


def original_title(item: Dict[str, Any]) -> str:
    """中文标题与原文不同时返回原文，供正文对照。"""
    orig = str(item.get("name") or "").strip()
    zh = str(item.get("name_zh") or "").strip()
    if orig and zh and orig != zh:
        return orig
    return ""


def display_price(item: Dict[str, Any]) -> str:
    jpy = int(item.get("price") or 0)
    text = f"{jpy:,}日元"
    cny = item.get("price_cny")
    if cny is None:
        return text
    return f"{text}（约{int(cny)}元）"


def fx_footer(items: List[Dict[str, Any]]) -> str:
    for it in items:
        label = str(it.get("fx_label") or "").strip()
        rate = it.get("fx_cny_per_jpy")
        if rate:
            tail = f"（{label}）" if label else ""
            return f"参考汇率：1日元 ≈ {float(rate):.5f}元{tail}"
    return ""


def _fx_cfg() -> Dict[str, Any]:
    raw = settings.CONFIG.get("fx") if isinstance(settings.CONFIG, dict) else None
    return raw if isinstance(raw, dict) else {}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_fx_file() -> Optional[Dict[str, Any]]:
    try:
        with open(_FX_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("rate"):
            return data
    except Exception:
        return None
    return None


def _write_fx_file(rate: float, label: str, day: str) -> None:
    os.makedirs(os.path.dirname(_FX_CACHE_PATH), exist_ok=True)
    payload = {
        "date": day,
        "rate": rate,
        "label": label,
        "fetched_at": int(time.time()),
    }
    with open(_FX_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def jpy_cny_rate() -> Tuple[Optional[float], str]:
    """返回 (1日元兑人民币, 来源说明)。同一天只请求一次，失败时不编造汇率。"""
    day = _today()
    cached = _fx_cache.get("rate")
    if cached and _fx_cache.get("date") == day:
        return float(cached), str(_fx_cache.get("label") or "")

    stored = _read_fx_file()
    if stored and str(stored.get("date") or "") == day:
        rate = float(stored["rate"])
        label = str(stored.get("label") or "")
        _fx_cache["rate"] = rate
        _fx_cache["date"] = day
        _fx_cache["label"] = label
        logger.info("日元汇率使用当日缓存: 1 JPY = %s CNY", rate)
        return rate, label

    rate, label = _fetch_jpy_cny()
    if rate is None or rate <= 0:
        return None, ""
    _fx_cache["rate"] = rate
    _fx_cache["date"] = day
    _fx_cache["label"] = label
    try:
        _write_fx_file(rate, label, day)
    except Exception as exc:
        logger.warning("汇率缓存写入失败: %s", exc)
    logger.info("日元汇率: 1 JPY = %s CNY (%s)", rate, label)
    return rate, label


def _fetch_jpy_cny() -> Tuple[Optional[float], str]:
    cfg = _fx_cfg()
    api_key = str(cfg.get("api_key") or "").strip()
    base = str(cfg.get("base") or "JPY").strip().upper() or "JPY"
    quote = str(cfg.get("quote") or "CNY").strip().upper() or "CNY"
    if not api_key:
        logger.warning("fx.api_key 为空，本次不附人民币价格")
        return None, ""
    url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{base}"
    last_error = ""
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("result") or "").lower() not in ("", "success"):
                raise RuntimeError(str(data.get("error-type") or data.get("result") or "汇率接口失败"))
            rates = data.get("conversion_rates") or data.get("rates") or {}
            rate = float(rates[quote])
            if rate <= 0:
                raise RuntimeError("汇率无效")
            updated = str(data.get("time_last_update_utc") or "").strip()
            label = "ExchangeRate-API"
            if updated:
                label = f"ExchangeRate-API {updated}"
            return rate, label
        except Exception as exc:
            last_error = str(exc)
            logger.warning("ExchangeRate-API 第 %s 次失败: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(1.5)
    logger.warning("ExchangeRate-API 汇率获取失败，本次不附人民币价格: %s", last_error)
    return None, ""


def translate_titles(titles: List[str]) -> List[str]:
    """译成简体中文。失败时原样返回，不阻断推送。"""
    if not titles:
        return []
    cfg = settings.DEEPSEEK or {}
    api_key = str(cfg.get("api_key") or "").strip()
    if not cfg.get("enabled", True) or not api_key:
        logger.warning("DeepSeek 未启用，标题保持原文")
        return list(titles)

    base_url = str(cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = str(cfg.get("model") or "deepseek-chat")
    timeout = int(cfg.get("timeout_seconds") or 60)
    out = list(titles)
    batch_size = 20
    for offset in range(0, len(titles), batch_size):
        batch = titles[offset : offset + batch_size]
        translated = _translate_batch(batch, base_url=base_url, model=model, api_key=api_key, timeout=timeout)
        for i, text in enumerate(translated):
            if text:
                out[offset + i] = text
    return out


def _translate_batch(
    titles: List[str],
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: int,
) -> List[str]:
    payload = [{"index": i, "title": t} for i, t in enumerate(titles)]
    prompt = f"""把下列二手商品标题译成简体中文。
要求：
- 只翻译标题里已有的信息，不补充、不评价
- 品牌用常见中文名（例如アークテリクス译为始祖鸟），型号、尺码、颜色保留
- 已经是中文的保持原样
- 每条都必须返回

输入：
{json.dumps(payload, ensure_ascii=False)}

仅输出 JSON，不要 markdown：
{{"translations":[{{"index":0,"text":"中文标题"}}]}}
"""
    try:
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你只输出合法 JSON，不输出其它文字。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        rows = parsed.get("translations") if isinstance(parsed, dict) else parsed
        by_index: Dict[int, str] = {}
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and "index" in row:
                    by_index[int(row["index"])] = str(row.get("text") or "").strip()
        result = []
        for i, title in enumerate(titles):
            result.append(by_index.get(i) or title)
        return result
    except Exception as exc:
        logger.warning("标题翻译失败，本批保持原文: %s", exc)
        return list(titles)


def prepare_push_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """复制商品并写入 name_zh、price_cny、汇率。"""
    prepared = [dict(it) for it in items]
    titles = [str(it.get("name") or "") for it in prepared]
    translated = translate_titles(titles)
    rate, label = jpy_cny_rate()
    for it, zh in zip(prepared, translated):
        it["name_zh"] = (zh or str(it.get("name") or "")).strip()
        jpy = int(it.get("price") or 0)
        it["price_jpy"] = jpy
        if rate:
            it["fx_cny_per_jpy"] = rate
            it["fx_label"] = label
            it["price_cny"] = int(round(jpy * rate))
    logger.info(
        "推送整理: %s 件，汇率=%s",
        len(prepared),
        f"{rate:.5f}" if rate else "无",
    )
    return prepared
