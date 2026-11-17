"""JPGOODBUY 官网轨迹：纯 HTTP，无需浏览器 / 17track。"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

TRACK_PAGE = "https://www.jpgoodbuy.com/package_track-{track_id}.html"


def track_page_url(track_id: str) -> str:
    return TRACK_PAGE.format(track_id=quote(track_id, safe=""))


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _extract_timeline(plain: str, track_id: str) -> str:
    """尽量截取运单号附近的轨迹正文，去掉整站导航噪音。"""
    if not plain:
        return ""
    markers = [f"运单号：{track_id}", f"运单号:{track_id}", track_id]
    start = -1
    for m in markers:
        start = plain.find(m)
        if start >= 0:
            break
    if start < 0:
        start = 0
    chunk = plain[start:]
    # 页脚常见截止词
    for stop in ("日本FBA跨境", "17TRACK", "使用指南", "服务条款", "联系我们", "商务合作"):
        p = chunk.find(stop)
        if p > 80:
            chunk = chunk[:p]
            break
    return re.sub(r"\s+", " ", chunk).strip()


def fetch_track_detail(
    track_id: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: float = 25.0,
) -> str:
    sess = session or requests.Session()
    url = track_page_url(track_id)
    r = sess.get(url, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    plain = _strip_html(r.content.decode("utf-8", "ignore"))
    return _extract_timeline(plain, track_id)


def fetch_many(
    track_ids: Iterable[str],
    *,
    delay_seconds: float = 0.35,
    timeout: float = 25.0,
) -> Dict[str, str]:
    sess = requests.Session()
    out: Dict[str, str] = {}
    ids: List[str] = [str(t).upper() for t in track_ids]
    for i, tid in enumerate(ids, 1):
        try:
            detail = fetch_track_detail(tid, session=sess, timeout=timeout)
            out[tid] = detail
            logger.info(
                "官网轨迹 %s/%s %s len=%s",
                i,
                len(ids),
                tid,
                len(detail or ""),
            )
        except Exception as e:
            logger.warning("官网轨迹失败 %s: %s", tid, e)
            out[tid] = ""
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return out
