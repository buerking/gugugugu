"""国际物流监控：Edge 仅抓后台 CN*JP；轨迹优先 17TRACK API，失败回退追跡なう。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from src import settings
from src.logistics.edge_cdp import EDGE_BINARY, EdgeCDP, default_logistics_user_data
from src.logistics import track_17track, track_tsuiseki
from src.notify import email_push


logger = logging.getLogger(__name__)


class LoginPendingError(RuntimeError):
    """登录/验证码未通过，需人工辅助；应用已有单号继续查轨迹。"""


TRACK_RE = re.compile(r"\bCN\d+JP\b", re.IGNORECASE)
# 稀有状态优先，避免「派送」抢先吞掉更具体的提醒
DEFAULT_KEYWORDS = (
    "等待用户缴税",
    "提交申报材料",
    "地址待确认",
    "您的邮件已",
    "派送",
)
# 追跡なう多为日文，作别名匹配
DEFAULT_KEYWORD_ALIASES = {
    "等待用户缴税": ["税関検査のため税関へ提示", "通関手続中", "課税", "税関"],
    "提交申报材料": ["通関手続", "税関へ提示", "税関検査"],
    "地址待确认": [
        "收件人详细地址待确认",
        "与收件人再次联系",
        "地址不详",
        "无法联系收件人",
        "联系不上收件人",
        "Retention",
    ],
    "您的邮件已": ["ご不在", "保管", "郵便物をお預かり"],
    "派送": ["配達中", "お届け先にお届け", "配達"],
}
ADMIN_LIST_URL = (
    "https://edi.jpgoodbuy.com/yii/web/index.php?"
    "r=admin/all-indent/get-all-zy-indent"
    "&ModuleName=-1&order_paymentstatus=-1&order_servicetype=-1"
    "&order_iscolsed=-1&order_isdelivery=-1&order_printstatus=-1"
    "&practical_num=0&ords_serviceid=0&storage_code=GMCWD&order_time=-1"
    "&rank=1&HouseId=0&order_type=-1&order_is_delivery_complete=0"
    "&order_isdelete=0&is_contraband=0&pageIndex=1&pageSize=20"
)


def _logistics_cfg() -> Dict[str, Any]:
    return dict(settings.CONFIG.get("logistics") or {})


def _seen_path() -> str:
    cfg = _logistics_cfg()
    rel = cfg.get("seen_path") or "data/logistics_seen.json"
    return settings.abs_path(rel)


def _empty_seen() -> Dict[str, Any]:
    return {"track_ids": [], "scraped_at": None, "tracks": {}}


def load_logistics_seen() -> Dict[str, Any]:
    """
    JSON 状态：
    {
      "track_ids": ["CN...JP", ...],   # 每日 Edge 抓号全量覆盖
      "scraped_at": 1234567890,
      "tracks": {
        "CN...JP": {
          "detail_hash": "...",
          "keyword": "等待用户缴税",
          "detail": "最近一次已推送/已记录的轨迹全文",
          "notified": true,
          "updated_at": 123,
          "notified_at": 123
        }
      }
    }
    内容 hash 不变 → 跳过；内容变了且「最新一条」仍命中关键词 → 再推送。
    """
    path = _seen_path()
    if not os.path.exists(path):
        return _empty_seen()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_seen()
        data.setdefault("tracks", {})
        if not isinstance(data["tracks"], dict):
            data["tracks"] = {}
        ids = data.get("track_ids")
        if not isinstance(ids, list):
            data["track_ids"] = []
        else:
            data["track_ids"] = [str(x).upper() for x in ids if str(x).strip()]
        if "scraped_at" not in data:
            data["scraped_at"] = None
        return data
    except Exception:
        return _empty_seen()


def save_logistics_seen(data: Dict[str, Any]) -> None:
    path = _seen_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = dict(data)
    payload.setdefault("track_ids", [])
    payload.setdefault("tracks", {})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_saved_track_ids() -> List[str]:
    """读取 JSON 中已存的单号列表（不启浏览器）。"""
    state = load_logistics_seen()
    ids = state.get("track_ids") or []
    out: List[str] = []
    seen: Set[str] = set()
    for x in ids:
        tid = str(x).upper().strip()
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def save_track_ids(track_ids: List[str], *, scraped_at: Optional[int] = None) -> Dict[str, Any]:
    """全量覆盖写入当日后台单号列表，保留 tracks 推送状态。"""
    state = load_logistics_seen()
    uniq: List[str] = []
    seen: Set[str] = set()
    for x in track_ids:
        tid = str(x).upper().strip()
        if tid and tid not in seen:
            seen.add(tid)
            uniq.append(tid)
    state["track_ids"] = uniq
    state["scraped_at"] = int(scraped_at if scraped_at is not None else time.time())
    save_logistics_seen(state)
    logger.info("已写入 %s 个单号到 %s", len(uniq), _seen_path())
    return state


def detail_hash(detail: str) -> str:
    norm = re.sub(r"\s+", " ", (detail or "").strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _edge_user_data() -> str:
    """自动化专用 Edge 目录（默认配置无法可靠开 CDP；绝不用 Chrome 目录）。"""
    cfg = _logistics_cfg()
    override = str(cfg.get("edge_user_data") or "").strip()
    if override:
        return override
    return default_logistics_user_data()


def _edge_profile() -> str:
    cfg = _logistics_cfg()
    return str(cfg.get("edge_profile") or "Default").strip() or "Default"


def build_edge() -> EdgeCDP:
    """只启动本机 Edge（msedge.exe），不碰 Chrome；调试口避开 Chrome 的 9222。"""
    from src.logistics.edge_cdp import resolve_edge_binary

    cfg = _logistics_cfg()
    preferred = str(cfg.get("edge_binary") or EDGE_BINARY).strip() or EDGE_BINARY
    binary = resolve_edge_binary(preferred)
    port = int(cfg.get("edge_debug_port") or 9333)
    edge = EdgeCDP(
        user_data_dir=_edge_user_data(),
        profile_directory=_edge_profile(),
        edge_binary=binary,
        port=port,
        sync_login=bool(cfg.get("edge_sync_login", True)),
    )
    edge.start()
    return edge


def _load_edi_credentials() -> Dict[str, str]:
    cfg = _logistics_cfg()
    # 优先 logistics 内联；否则读独立凭证文件
    user = str(cfg.get("edi_username") or "").strip()
    password = str(cfg.get("edi_password") or "").strip()
    if user and password:
        return {"username": user, "password": password}
    path = settings.abs_path(
        str(cfg.get("edi_credentials_path") or "config/edi_credentials.json")
    )
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "username": str(data.get("username") or "").strip(),
            "password": str(data.get("password") or "").strip(),
        }
    except Exception as e:
        logger.warning("读取 edi 凭证失败: %s", e)
        return {}


def _fill_edi_login(edge: EdgeCDP, username: str, password: str) -> bool:
    """填写铭东/edi 登录表单。"""
    user_sels = [
        "input[name='username']",
        "input[name='admin_name']",
        "input[name='user_name']",
        "input[type='text']",
        "input[placeholder*='账号']",
        "input[placeholder*='用户']",
    ]
    pass_sels = [
        "input[name='password']",
        "input[name='admin_password']",
        "input[type='password']",
    ]
    filled_user = False
    filled_pass = False
    for sel in user_sels:
        if edge.fill_selector(sel, username):
            filled_user = True
            break
    for sel in pass_sels:
        if edge.fill_selector(sel, password):
            filled_pass = True
            break
    logger.info("登录表单填写: user=%s pass=%s", filled_user, filled_pass)
    return filled_user and filled_pass


def _has_order_table(edge: EdgeCDP) -> bool:
    try:
        return bool(edge.evaluate("!!document.querySelector('table.public_table')"))
    except Exception:
        return False


def _has_login_form(edge: EdgeCDP) -> bool:
    try:
        return bool(edge.evaluate("!!document.querySelector('button.submit_btn')"))
    except Exception:
        return False


def _has_captcha(edge: EdgeCDP) -> bool:
    """检测点击/滑动类验证码弹层（勿强刷，需人工点选）。"""
    js = """
(() => {
  const sels = [
    '.geetest_panel', '.geetest_popup', '.geetest_wind', '.geetest_holder',
    '.geetest_panel_box', '#captcha', '.captcha', '.nc-container',
    '.yidun_popup', '.yidun_panel', '.yidun_modal',
    '[class*="captcha"]', '[id*="captcha"]',
    'iframe[src*="captcha"]', 'iframe[src*="geetest"]',
    'iframe[src*="verify"]', 'iframe[src*="nvc"]',
  ];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    const st = window.getComputedStyle(el);
    if (st && (st.display === 'none' || st.visibility === 'hidden')) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 20 && r.height > 20) return true;
  }
  const t = (document.body && document.body.innerText) || '';
  if (/点击验证|点选验证|滑动验证|完成验证|请完成安全验证|拖动滑块/.test(t)) return true;
  return false;
})()
"""
    try:
        return bool(edge.evaluate(js))
    except Exception:
        return False


def _login_wait_seconds() -> float:
    cfg = _logistics_cfg()
    return float(cfg.get("login_captcha_wait_seconds") or 180)


def _wait_login_ready(edge: EdgeCDP, *, timeout: Optional[float] = None) -> None:
    """提交登录后耐心等待：出现订单表即成功；有验证码则只等、不刷新、不重复点登录。"""
    wait_s = timeout if timeout is not None else _login_wait_seconds()
    deadline = time.time() + wait_s
    captcha_hinted = False
    last_log = 0.0
    while time.time() < deadline:
        if _has_order_table(edge):
            if captcha_hinted:
                logger.info("验证码已通过，订单列表已出现")
            return
        if _has_captcha(edge):
            if not captcha_hinted:
                captcha_hinted = True
                logger.info(
                    "登录出现点击验证码，请在 Edge 窗口内手动完成；"
                    "请耐心等待，不要强刷/关闭页面（最长等待 %.0f 秒）",
                    wait_s,
                )
            now = time.time()
            if now - last_log >= 20:
                remain = max(0, int(deadline - now))
                logger.info("仍在等待验证码完成… 剩余约 %s 秒", remain)
                last_log = now
            time.sleep(1.0)
            continue
        # 仍在登录页但无验证码：可能刚提交、或需再点一次登录（仅未提示过验证码时）
        if _has_login_form(edge) and not captcha_hinted:
            time.sleep(0.8)
            continue
        time.sleep(0.8)
    if captcha_hinted:
        raise LoginPendingError(
            f"等待验证码超时（{wait_s:.0f}s）。抓号暂缓，请稍后人工在 Edge 完成验证；"
            "守护将继续用已有单号定时查轨迹。"
        )
    raise LoginPendingError(
        "登录未通过（未进入订单列表）。抓号暂缓，等待人工辅助登录；"
        "守护将继续用已有单号定时查轨迹。"
    )


def _maybe_login(edge: EdgeCDP, timeout: float = 15.0) -> None:
    creds = _load_edi_credentials()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_order_table(edge):
            return
        if _has_captcha(edge):
            logger.info(
                "检测到验证码，请在 Edge 窗口手动完成；耐心等待，不要强刷"
            )
            _wait_login_ready(edge)
            return
        has_btn = _has_login_form(edge)
        if not has_btn:
            time.sleep(0.4)
            continue
        if creds.get("username") and creds.get("password"):
            _fill_edi_login(edge, creds["username"], creds["password"])
            time.sleep(0.3)
        clicked = edge.click_selector("button.submit_btn")
        if clicked:
            logger.info("检测到登录页，已提交登录")
            logger.info(
                "若弹出点击验证码，请在 Edge 内手动点选；启动后请耐心等待，不要强刷"
            )
            _wait_login_ready(edge)
            return
        time.sleep(0.5)
    if _has_order_table(edge):
        return
    if _has_captcha(edge) or _has_login_form(edge):
        _wait_login_ready(edge)
        return
    logger.info("未出现登录按钮，视为已登录或已在后台")


def _wait_order_table(edge: EdgeCDP, timeout: float = 30.0) -> None:
    # 登录/验证码阶段用更长等待；列表翻页用较短超时
    if _has_captcha(edge) or _has_login_form(edge):
        _wait_login_ready(edge, timeout=max(timeout, _login_wait_seconds()))
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_order_table(edge):
            return
        if _has_captcha(edge):
            _wait_login_ready(edge)
            return
        # 仍在登录页：只再点一次登录，之后交给验证码等待逻辑，禁止循环强刷
        if _has_login_form(edge):
            if edge.click_selector("button.submit_btn"):
                logger.info("等待表格时补点一次登录（之后若有验证码将耐心等待，不刷新）")
                _wait_login_ready(edge)
                return
        time.sleep(0.5)
    raise LoginPendingError(
        "等待订单表格超时。抓号暂缓，等待人工辅助登录；"
        "守护将继续用已有单号定时查轨迹。"
    )


def _extract_tracks_from_html(html: str) -> List[str]:
    found = TRACK_RE.findall(html or "")
    out: List[str] = []
    seen: Set[str] = set()
    for t in found:
        u = t.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _total_pages(edge: EdgeCDP) -> int:
    try:
        val = edge.evaluate(
            "((el)=> el ? (el.value || el.getAttribute('value') || '1') : '1')"
            "(document.querySelector('#totalPage'))"
        )
        return max(1, int(val or 1))
    except Exception:
        return 1


def _page_url(page_index: int, page_size: int = 20) -> str:
    base = str(_logistics_cfg().get("admin_list_url") or ADMIN_LIST_URL)
    base = re.sub(r"pageIndex=\d+", f"pageIndex={page_index}", base)
    base = re.sub(r"pageSize=\d+", f"pageSize={page_size}", base)
    return base


def scrape_tracking_numbers(
    edge: EdgeCDP,
    *,
    max_pages: Optional[int] = None,
    page_size: int = 20,
) -> List[str]:
    list_url = _page_url(1, page_size)
    edge.navigate(list_url, wait_seconds=3)
    _maybe_login(edge)
    # 登录后若仍在登录页：只耐心等验证码/跳转，禁止再 navigate 强刷（会打断点选验证码）
    if not _has_order_table(edge):
        _wait_order_table(edge, timeout=_login_wait_seconds())

    total = _total_pages(edge)
    # 默认只抓第 1 页；配置 max_pages 可扩大，null/0 仍按 1 页
    limit = 1 if max_pages is None else max(1, int(max_pages))
    fetch_pages = min(total, limit)
    logger.info("订单列表共 %s 页（本次抓取 %s 页）", total, fetch_pages)

    all_tracks: List[str] = []
    seen: Set[str] = set()
    for page in range(1, fetch_pages + 1):
        if page > 1:
            edge.navigate(_page_url(page, page_size), wait_seconds=2.5)
            _wait_order_table(edge)
        tracks = _extract_tracks_from_html(edge.page_source())
        logger.info("第 %s 页抓到 %s 个单号: %s", page, len(tracks), tracks)
        for t in tracks:
            if t not in seen:
                seen.add(t)
                all_tracks.append(t)
        time.sleep(0.4)
    return all_tracks


def _keyword_patterns(keywords: List[str]) -> List[Tuple[str, List[str]]]:
    """返回 [(主关键词, [主词+别名...]), ...]。"""
    cfg = _logistics_cfg()
    aliases = dict(DEFAULT_KEYWORD_ALIASES)
    raw = cfg.get("keyword_aliases") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, list):
                aliases[str(k)] = [str(x) for x in v]
    out: List[Tuple[str, List[str]]] = []
    for kw in keywords:
        pats = [kw] + list(aliases.get(kw) or [])
        # 去重保序
        seen: Set[str] = set()
        uniq = []
        for p in pats:
            if p and p not in seen:
                seen.add(p)
                uniq.append(p)
        out.append((kw, uniq))
    return out


def _status_record(payload: Any) -> Dict[str, str]:
    """统一为 {detail, latest}。旧版纯字符串视为全文，并尽量抽出「最新：」行。"""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or "")
        latest = str(payload.get("latest") or "").strip()
        if not latest and detail:
            latest = _latest_from_flat_detail(detail)
        return {"detail": detail, "latest": latest}
    detail = str(payload or "")
    return {"detail": detail, "latest": _latest_from_flat_detail(detail)}


def _latest_from_flat_detail(detail: str) -> str:
    """从 flatten 全文中抽出「最新：」行；没有则退回「当前：」行。"""
    if not detail:
        return ""
    for line in detail.splitlines():
        s = line.strip()
        if s.startswith("最新："):
            return s[len("最新：") :].strip()
        if s.startswith("当前："):
            return s[len("当前：") :].strip()
    return ""


def match_keywords(
    statuses: Dict[str, Any],
    keywords: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    只对「最新一条」做关键词匹配；历史轨迹仅随邮件附带，不参与命中。
    例如曾出现「等待用户缴税」、最新已变为派送/地址确认 → 不再用缴税提醒。
    """
    kws = list(keywords or _logistics_cfg().get("keywords") or DEFAULT_KEYWORDS)
    patterns = _keyword_patterns(kws)
    hits: List[Dict[str, str]] = []
    miss = 0
    for tid, payload in statuses.items():
        rec = _status_record(payload)
        detail = rec.get("detail") or ""
        latest = (rec.get("latest") or "").strip()
        if not latest:
            # 无最新字段时不拿全文滥匹配，避免历史误命中
            miss += 1
            continue
        matched = False
        for main_kw, pats in patterns:
            if any(p in latest for p in pats):
                hits.append(
                    {
                        "track_id": tid,
                        "keyword": main_kw,
                        "detail": detail,
                        "latest": latest,
                    }
                )
                logger.info(
                    "命中 %s：keyword=%s | 最新=%s",
                    tid,
                    main_kw,
                    latest[:120],
                )
                matched = True
                break
        if not matched:
            miss += 1
    logger.info(
        "关键词匹配（仅最新一条）：命中 %s，未命中 %s",
        len(hits),
        miss,
    )
    return hits


def filter_hits_by_change(
    hits: List[Dict[str, str]],
    *,
    state: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, Any]]:
    """
    按 JSON 中已存轨迹内容判断：
    - 内容未变（hash 相同）→ 跳过（即便仍匹配关键词）
    - 内容变了或首次命中 → 需要推送
    返回 (need_push, skipped, state)
    """
    state = state if state is not None else load_logistics_seen()
    tracks: Dict[str, Any] = state.setdefault("tracks", {})
    need: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    now = int(time.time())
    for h in hits:
        tid = str(h.get("track_id") or "").upper()
        detail = h.get("detail") or ""
        hsh = detail_hash(detail)
        prev = tracks.get(tid) if isinstance(tracks.get(tid), dict) else None
        prev_hash = str((prev or {}).get("detail_hash") or "")
        if prev_hash and prev_hash == hsh:
            skipped.append({**h, "reason": "内容未变化"})
            # 仍刷新 last_seen
            tracks[tid] = {
                **(prev or {}),
                "detail_hash": hsh,
                "keyword": h.get("keyword"),
                "detail": detail,
                "updated_at": now,
                "notified": bool((prev or {}).get("notified")),
                "notified_at": (prev or {}).get("notified_at"),
            }
            logger.info("跳过 %s：轨迹内容未变化 hash=%s", tid, hsh[:8])
            continue
        need.append({**h, "detail_hash": hsh, "prev_hash": prev_hash})
        logger.info(
            "待推送 %s：keyword=%s %s",
            tid,
            h.get("keyword"),
            "首次" if not prev_hash else f"内容变更 {prev_hash[:8]}->{hsh[:8]}",
        )
    return need, skipped, state


def send_logistics_emails(
    subscription: Dict[str, Any],
    hits: List[Dict[str, str]],
    *,
    skip_unchanged: bool = True,
    max_send: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    命中关键词后：仅当轨迹全文相对 JSON 有变化时发信；发信后写回 JSON。
    返回 {sent, skipped, state}
    """
    if not hits:
        state = load_logistics_seen()
        return {"sent": [], "skipped": [], "state": state}

    if skip_unchanged:
        need, skipped, state = filter_hits_by_change(hits)
    else:
        state = load_logistics_seen()
        need = [{**h, "detail_hash": detail_hash(h.get("detail") or "")} for h in hits]
        skipped = []

    tracks: Dict[str, Any] = state.setdefault("tracks", {})
    sent: List[Dict[str, str]] = []
    now = int(time.time())

    for h in need:
        if max_send is not None and len(sent) >= max_send:
            logger.info("已达本次发送上限 max_send=%s", max_send)
            break
        tid = str(h["track_id"]).upper()
        kw = h.get("keyword") or ""
        detail = h.get("detail") or ""
        latest = h.get("latest") or _latest_from_flat_detail(detail)
        hsh = h.get("detail_hash") or detail_hash(detail)
        subject = f"【物流提醒】{tid} {kw}"
        body = (
            f"单号：{tid}\n"
            f"匹配关键词：{kw}\n"
            f"匹配依据（仅最新一条）：{latest}\n"
            f"17TRACK：https://t.17track.net/zh-cn#nums={tid}\n"
            f"\n—— 轨迹全文（仅供参考，匹配不看历史）——\n{detail}\n"
        )
        if dry_run:
            logger.info("[dry_run] 将发送: %s %s", tid, kw)
            sent.append(h)
            continue
        email_push.send_plain(subscription, subject, body)
        logger.info("物流邮件已发: %s %s", tid, kw)
        tracks[tid] = {
            "detail_hash": hsh,
            "keyword": kw,
            "detail": detail,
            "notified": True,
            "updated_at": now,
            "notified_at": now,
        }
        sent.append(h)

    # dry_run 不写 JSON，避免污染「已推送」状态
    if not dry_run:
        save_logistics_seen(state)
    return {"sent": sent, "skipped": skipped, "state": state}


def fetch_statuses(track_ids: List[str]) -> Dict[str, Dict[str, str]]:
    """
    优先 17TRACK API；无有效轨迹的单号再回退追跡なう。
    返回 track_id -> {"detail": 全文, "latest": 最新一条}。
    """
    cfg = _logistics_cfg()
    token = str(cfg.get("track17_api_token") or "").strip()
    lang = str(cfg.get("track17_lang") or "zh").strip() or "zh"
    out: Dict[str, Dict[str, str]] = {}

    if token:
        try:
            out = track_17track.fetch_many(
                token,
                track_ids,
                lang=lang,
                wait_seconds=float(cfg.get("track17_wait_seconds") or 4),
                retries=int(cfg.get("track17_retries") or 4),
                retry_interval=float(cfg.get("track17_retry_interval") or 3),
            )
        except Exception as e:
            logger.warning("17TRACK 整批失败，将全部回退追跡なう: %s", e)
            out = {}
    else:
        logger.warning("未配置 logistics.track17_api_token，直接用追跡なう")

    def _has_detail(rec: Any) -> bool:
        if isinstance(rec, dict):
            return bool(str(rec.get("detail") or "").strip())
        return bool(str(rec or "").strip())

    missing = [t for t in track_ids if not _has_detail(out.get(t))]
    if missing:
        logger.info("17TRACK 未返回 %s 个单号，回退追跡なう", len(missing))
        fb = track_tsuiseki.fetch_many(
            missing,
            carrier=str(cfg.get("tsuiseki_carrier") or "post"),
            delay_seconds=float(cfg.get("tsuiseki_delay_seconds") or 0.4),
            timeout=float(cfg.get("track_http_timeout") or 25),
        )
        for tid, rec in fb.items():
            if _has_detail(rec):
                out[tid] = rec if isinstance(rec, dict) else {"detail": str(rec), "latest": ""}
    return out


def scrape_and_save_track_ids(
    *,
    max_pages: Optional[int] = None,
    keep_browser_open: bool = False,
) -> Dict[str, Any]:
    """
    仅用 Edge 抓 edi 后台 CN*JP，写入 JSON 的 track_ids 后关闭浏览器。
    不查轨迹、不发邮件。
    登录/验证码未通过时不抛异常，返回 login_pending=True，由守护改用已有单号查轨迹。
    """
    cfg = _logistics_cfg()
    if max_pages is None:
        raw = cfg.get("max_pages")
        max_pages = 1 if raw is None else max(1, int(raw))

    edge: Optional[EdgeCDP] = None
    try:
        edge = build_edge()
        tracks = scrape_tracking_numbers(edge, max_pages=max_pages)
        if not tracks:
            logger.warning("未抓到任何 CN*JP 单号")
        else:
            save_track_ids(tracks)
        return {
            "ok": True,
            "login_pending": False,
            "tracks": tracks,
            "track_ids": tracks,
            "scraped_at": int(time.time()) if tracks else None,
            "count": len(tracks),
        }
    except LoginPendingError as exc:
        existing = get_saved_track_ids()
        logger.warning("%s", exc)
        logger.warning(
            "本次未更新单号；已有 %s 个单号将按原节奏查轨迹，抓号待人工辅助登录后再试",
            len(existing),
        )
        return {
            "ok": False,
            "login_pending": True,
            "error": str(exc),
            "tracks": [],
            "track_ids": [],
            "scraped_at": None,
            "count": 0,
            "existing_count": len(existing),
        }
    finally:
        if edge is not None and not keep_browser_open:
            try:
                edge.close()
                logger.info("Edge 已关闭（抓号结束）")
            except Exception:
                pass


def poll_and_notify(
    subscription: Dict[str, Any],
    *,
    track_ids: Optional[List[str]] = None,
    skip_unchanged: bool = True,
    max_send: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    不启浏览器：读已存单号 → 17TRACK/追跡なう → 关键词 + hash → 发邮件。
    track_ids 为空则从 JSON 读取；仍为空则跳过。
    """
    cfg = _logistics_cfg()
    keywords = list(cfg.get("keywords") or DEFAULT_KEYWORDS)

    if track_ids is None:
        tracks = get_saved_track_ids()
    else:
        tracks = [str(t).upper() for t in track_ids if str(t).strip()]

    if not tracks:
        logger.warning("无可用单号（JSON track_ids 为空），跳过查轨迹")
        return {
            "tracks": [],
            "hits": [],
            "sent": [],
            "skipped": [],
            "statuses": {},
        }

    statuses = fetch_statuses(tracks)
    hits = match_keywords(statuses, keywords)
    prio = {k: i for i, k in enumerate(keywords)}
    hits.sort(key=lambda h: prio.get(h.get("keyword") or "", 99))

    result = send_logistics_emails(
        subscription,
        hits,
        skip_unchanged=skip_unchanged,
        max_send=max_send,
        dry_run=dry_run,
    )
    return {
        "tracks": tracks,
        "hits": hits,
        "sent": result.get("sent") or [],
        "skipped": result.get("skipped") or [],
        "statuses": statuses,
    }


def run_logistics_once(
    subscription: Dict[str, Any],
    *,
    max_pages: Optional[int] = None,
    skip_unchanged: bool = True,
    keep_browser_open: bool = False,
    max_send: Optional[int] = None,
    dry_run: bool = False,
    scrape: bool = True,
    poll: bool = True,
    # 兼容旧参数名
    skip_seen: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    手工/干跑入口。
    scrape=True：现场 Edge 抓号并写 JSON；False：只用已存 track_ids。
    poll=True：查轨迹并推送；False：只抓号。
    """
    if skip_seen is not None:
        skip_unchanged = bool(skip_seen)

    tracks: List[str] = []
    if scrape:
        scrape_result = scrape_and_save_track_ids(
            max_pages=max_pages,
            keep_browser_open=keep_browser_open,
        )
        tracks = list(scrape_result.get("tracks") or [])
        login_pending = bool(scrape_result.get("login_pending"))
        if not poll:
            return {
                "tracks": tracks,
                "hits": [],
                "sent": [],
                "skipped": [],
                "statuses": {},
                "login_pending": login_pending,
                "scrape_error": scrape_result.get("error"),
            }
        # 登录未过或本次未抓到号：改用已存单号继续查轨迹
        if not tracks:
            existing = get_saved_track_ids()
            if login_pending:
                logger.warning(
                    "抓号因登录未通过暂缓，改用已存单号 %s 个查轨迹",
                    len(existing),
                )
            if not existing:
                return {
                    "tracks": [],
                    "hits": [],
                    "sent": [],
                    "skipped": [],
                    "statuses": {},
                    "login_pending": login_pending,
                    "scrape_error": scrape_result.get("error"),
                }
            out = poll_and_notify(
                subscription,
                track_ids=existing,
                skip_unchanged=skip_unchanged,
                max_send=max_send,
                dry_run=dry_run,
            )
            out["login_pending"] = login_pending
            out["scrape_error"] = scrape_result.get("error")
            out["tracks"] = existing
            return out
        return poll_and_notify(
            subscription,
            track_ids=tracks,
            skip_unchanged=skip_unchanged,
            max_send=max_send,
            dry_run=dry_run,
        )

    if not poll:
        tracks = get_saved_track_ids()
        return {
            "tracks": tracks,
            "hits": [],
            "sent": [],
            "skipped": [],
            "statuses": {},
        }

    return poll_and_notify(
        subscription,
        track_ids=None,
        skip_unchanged=skip_unchanged,
        max_send=max_send,
        dry_run=dry_run,
    )
