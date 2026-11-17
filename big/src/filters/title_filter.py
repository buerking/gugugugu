"""
标题关键词过滤 + GPU 场景启发式（整机/配件/错型号）。
对齐 sub-push keyword_title_filter，并加强专业卡场景。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Sequence, Tuple


logger = logging.getLogger(__name__)

_STOPLIST = frozenset(
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

# 整机/工作站信号（标题含目标型号但仍是整机时剔除）
_PC_SIGNALS = (
    "ワークステーション",
    "workstation",
    "thinkstation",
    "thinkpad",
    "precision",
    "デスクトップ",
    "desktop",
    "タワー",
    "tower",
    "xeon",
    "core i",
    "corei",
    "ryzen",
    "メモリ",
    "ssd",
    "hdd",
    "win10",
    "win11",
    "windows",
    "クリエイター向け",
    "z4 g4",
    "z440",
    "z8 g4",
    "z240",
    "pavilion",
    "パソコン",
    "デスクトップpc",
    "搭載",
)

_ACCESSORY_SIGNALS = (
    "ブラケット",
    "bracket",
    "ケーブルのみ",
    "ケーブルだけ",
    "空箱",
    "箱のみ",
    "マニュアルのみ",
    "説明書のみ",
)

# 近邻错型号：搜索 P4000 时排除 P400/P600 等（用边界匹配）
_NEAR_MISS_PATTERNS = {
    "p4000": [r"p400(?!\d)", r"p600(?!\d)", r"p620(?!\d)", r"p1000(?!\d)", r"p2000(?!\d)"],
    "p2000": [r"p400(?!\d)", r"p600(?!\d)", r"p4000", r"p5000", r"p6000"],
    "p2200": [r"p2000", r"p4000"],
    "p5000": [r"p4000", r"p6000", r"p500(?!\d)"],
    "p6000": [r"p600(?!\d)", r"p4000", r"p5000"],
}


def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", s.strip()).lower()


def tokenize_keyword(keyword: str) -> List[str]:
    kw = _normalize(keyword)
    if not kw:
        return []
    tokens: List[str] = []
    for part in re.split(r"[\s/+,，、・\-_]+", kw):
        p = part.strip()
        if not p or p in _STOPLIST:
            continue
        if len(p) == 1 and not re.match(r"[a-z0-9]", p):
            continue
        tokens.append(p)
    return tokens


def _has_model_token(title_n: str, token: str) -> bool:
    """
    含数字的型号用边界匹配，避免 p400 命中 p4000。
    """
    if not re.search(r"\d", token):
        return token in title_n
    # 允许前后为非字母数字
    pat = re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", re.I)
    return bool(pat.search(title_n))


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
        if not _has_model_token(title_n, t):
            return False

    # 有明确型号命中时，品牌词（quadro/nvidia 等）视为可选，避免「仅写 P4000」被拒
    if model_tokens:
        return True

    if not other_tokens:
        return True
    matched = sum(1 for t in other_tokens if t in title_n)
    if len(other_tokens) <= 2:
        return matched == len(other_tokens)
    need = max(1, int(len(other_tokens) * 0.6 + 0.999))
    return matched >= need


def looks_like_unwanted_gpu_listing(title: str, *, reference_key: str = "") -> bool:
    """专业显卡订阅：剔除整机/配件等。"""
    if reference_key and reference_key != "gpu_quadro":
        return False
    title_n = _normalize(title)
    if any(x in title_n for x in _ACCESSORY_SIGNALS):
        # 纯配件
        if "ボード" not in title_n and "quadro" not in title_n and "rtx" not in title_n:
            return True
        if "ブラケット" in title_n or "bracket" in title_n:
            return True
    pc_hits = sum(1 for x in _PC_SIGNALS if x in title_n)
    if pc_hits >= 2:
        return True
    # 单条强信号 + 内存/存储/系统组合
    if any(
        x in title_n
        for x in (
            "workstation",
            "ワークステーション",
            "thinkstation",
            "precision",
            "thinkpad",
            "z4 g4",
            "z440",
            "z8 g4",
            "パソコン",
        )
    ):
        if any(x in title_n for x in ("xeon", "ssd", "hdd", "メモリ", "win11", "win10", "windows", "gb")):
            return True
    # 「XXGB」内存写法 + 机箱型号
    if re.search(r"\d+\s*gb", title_n) and any(
        x in title_n for x in ("xeon", "workstation", "ワークステーション", "hp z", "dell", "lenovo")
    ):
        return True
    return False


def is_on_sale_status(item: Dict[str, Any]) -> bool:
    status = str(item.get("status") or "").lower()
    if not status:
        return True
    bad = ("sold", "trading", "cancel", "stop", "delete")
    if any(b in status for b in bad):
        return False
    # mercari: item_status_on_sale / on_sale
    if "on_sale" in status or status in ("open", "unknown", "item_status_on_sale"):
        return True
    if status.startswith("item_status_") and "on_sale" not in status:
        return False
    return True


def filter_items_by_keyword(
    keyword: str,
    items: Sequence[Dict[str, Any]],
    *,
    reference_key: str = "",
) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for item in items:
        if not is_on_sale_status(item):
            dropped += 1
            continue
        title = str(item.get("name") or item.get("title") or "")
        kw = str(item.get("matched_keyword") or keyword)
        if not title_matches_keyword(kw, title):
            dropped += 1
            continue
        if looks_like_unwanted_gpu_listing(title, reference_key=reference_key):
            dropped += 1
            continue
        kept.append(item)
    return kept, dropped
