import json
import logging
import re
from typing import Any, Dict, List, Tuple

import requests

from src import settings


logger = logging.getLogger(__name__)


def load_reference_text(reference_key: str) -> str:
    path = settings.REFERENCE_ITEMS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        logger.warning("读取参考商品说明失败: %s", exc)
        return ""

    key = (reference_key or "default").strip() or "default"
    # 按 ## key 分节
    pattern = re.compile(rf"(?m)^##\s+{re.escape(key)}\s*$")
    m = pattern.search(content)
    if not m:
        # 回退整份文档
        return content.strip()
    start = m.end()
    nxt = re.search(r"(?m)^##\s+\S+", content[start:])
    end = start + nxt.start() if nxt else len(content)
    return content[start:end].strip() or content.strip()


def _build_prompt(reference: str, items: List[Dict[str, Any]]) -> str:
    compact = []
    for i, it in enumerate(items):
        compact.append(
            {
                "index": i,
                "id": it.get("id"),
                "source": it.get("source_site"),
                "listing_type": it.get("listing_type"),
                "title": it.get("name"),
                "price": it.get("price"),
                "url": it.get("item_url"),
            }
        )
    return f"""你是二手/拍卖商品审核助手。根据「标准可参考商品说明」，判断下列搜索结果是否真正符合用户需求。
要严格识别标题党：关键词碰巧匹配但实际商品无关、明显误导、纯空包装/无关周边等应判定不符合。

# 标准可参考商品说明
{reference}

# 待审商品（JSON）
{json.dumps(compact, ensure_ascii=False)}

# 输出要求
仅输出 JSON 对象，不要 markdown：
{{
  "results": [
    {{"index": 0, "match": true, "reason": "简短中文理由"}},
    {{"index": 1, "match": false, "reason": "简短中文理由"}}
  ]
}}
每个待审商品都必须有一条结果。
"""


def filter_items(
    items: List[Dict[str, Any]],
    *,
    reference_key: str = "default",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (通过列表, 拒绝列表)。DeepSeek 关闭时全部通过。"""
    cfg = settings.DEEPSEEK or {}
    if not items:
        return [], []
    if not cfg.get("enabled", True):
        return items, []

    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        logger.warning("DeepSeek api_key 为空，跳过 AI 过滤")
        return items, []

    reference = load_reference_text(reference_key)
    if not reference.strip():
        logger.warning("参考商品说明为空，跳过 AI 过滤")
        return items, []

    base_url = str(cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = str(cfg.get("model") or "deepseek-chat")
    timeout = int(cfg.get("timeout_seconds") or 60)

    # 分批，避免一次太长
    batch_size = 20
    passed: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for offset in range(0, len(items), batch_size):
        batch = items[offset : offset + batch_size]
        prompt = _build_prompt(reference, batch)
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
            results = parsed.get("results") if isinstance(parsed, dict) else parsed
            by_index: Dict[int, Dict[str, Any]] = {}
            if isinstance(results, list):
                for row in results:
                    if isinstance(row, dict) and "index" in row:
                        by_index[int(row["index"])] = row
            for i, it in enumerate(batch):
                row = by_index.get(i) or {"match": True, "reason": "AI未返回该项，默认通过"}
                match = bool(row.get("match"))
                reason = str(row.get("reason") or "")
                enriched = dict(it)
                enriched["ai_match"] = match
                enriched["ai_reason"] = reason
                if match:
                    passed.append(enriched)
                else:
                    rejected.append(enriched)
        except Exception as exc:
            logger.error("DeepSeek 过滤失败，本批默认全部通过: %s", exc)
            for it in batch:
                enriched = dict(it)
                enriched["ai_match"] = True
                enriched["ai_reason"] = f"AI失败默认通过: {exc}"
                passed.append(enriched)

    logger.info("DeepSeek 过滤: 输入=%s 通过=%s 拒绝=%s", len(items), len(passed), len(rejected))
    return passed, rejected
