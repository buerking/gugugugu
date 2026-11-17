"""
根据订阅关键词过滤商品标题，减少「接口返回但型号不符」的误推。

策略概要：
1. 关键词拆成 token，去掉过于泛化的停用词（如 グラフィックボード）。
2. 含数字的 token（如 P2000、4060）视为型号，必须全部出现在标题中。
3. 其余 token：数量少时要求全部命中；数量多时要求达到一定比例命中。
"""
import logging
import re
import unicodedata
from typing import Any, Dict, List, Sequence, Tuple

from src import settings


logger = logging.getLogger(__name__)

# 与具体型号无关、容易误伤召回的泛化词（小写 / NFKC 后比较）
_DEFAULT_STOPLIST = frozenset(
    {
        "グラフィックボード",
        "グラボ",
        "ビデオカード",
        "graphics",
        "graphic",
        "board",
        "card",
        "gpu",
        "美品",
        "新品",
        "未使用",
        "中古",
        "送料無料",
        "動作確認済",
        "即購入可",
    }
)


def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", s.strip()).lower()


def tokenize_keyword(keyword: str) -> List[str]:
    """从订阅关键词提取需要参与匹配的 token。"""
    kw = _normalize(keyword)
    if not kw:
        return []
    stoplist = _DEFAULT_STOPLIST | frozenset(
        _normalize(x) for x in settings.KEYWORD_TITLE_FILTER_STOPLIST
    )
    tokens: List[str] = []
    for part in re.split(r"[\s/+,，、・\-_]+", kw):
        p = part.strip()
        if not p or p in stoplist:
            continue
        # 单字符且非英数字则跳过
        if len(p) == 1 and not re.match(r"[a-z0-9]", p):
            continue
        tokens.append(p)
    return tokens


def title_matches_keyword(keyword: str, title: str) -> bool:
    tokens = tokenize_keyword(keyword)
    if not tokens:
        return True

    title_n = _normalize(title)
    if not title_n:
        return False

    model_tokens = [t for t in tokens if re.search(r"\d", t)]
    other_tokens = [t for t in tokens if t not in model_tokens]

    for t in model_tokens:
        if t not in title_n:
            return False

    if not other_tokens:
        return True

    matched = sum(1 for t in other_tokens if t in title_n)
    if len(other_tokens) <= 2:
        return matched == len(other_tokens)

    ratio = settings.KEYWORD_TITLE_FILTER_MIN_OTHER_RATIO
    need = max(1, int(len(other_tokens) * ratio + 0.999))
    return matched >= need


def filter_items_by_keyword(
    keyword: str,
    items: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    按标题与关键词匹配过滤商品列表。
    返回 (保留列表, 剔除数量)。
    """
    if not settings.KEYWORD_TITLE_FILTER_ENABLED:
        return list(items), 0

    kept: List[Dict[str, Any]] = []
    dropped = 0
    for item in items:
        title = item.get("name") or item.get("title") or ""
        if title_matches_keyword(keyword, title):
            kept.append(item)
        else:
            dropped += 1
            logger.debug(
                "标题过滤剔除: keyword=%s title=%s",
                keyword,
                (title[:80] + "…") if len(title) > 80 else title,
            )
    return kept, dropped
