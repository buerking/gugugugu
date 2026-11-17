"""本地密钥层：config.json（可结构） + secrets.local.json（仅密钥，不入库）。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Tuple

SECRET_PATHS: List[Tuple[str, ...]] = [
    ("deepseek", "api_key"),
    ("fx", "api_key"),
    ("wecom", "corp_id"),
    ("wecom", "corp_secret"),
    ("email", "smtp_user"),
    ("email", "smtp_password"),
    ("email", "from_addr"),
    ("yahoo_auction", "cookie"),
    ("mercari", "laplace_device_uuid"),
    ("logistics", "track17_api_token"),
    ("logistics", "admin_list_url"),
]


def _get_path(obj: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    cur: Any = obj
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set_path(obj: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    cur: Dict[str, Any] = obj
    for p in path[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[path[-1]] = value


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def split_secrets(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    public = deepcopy(cfg)
    secrets: Dict[str, Any] = {}
    for path in SECRET_PATHS:
        val = _get_path(cfg, path)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            _set_path(public, path, "")
            continue
        _set_path(secrets, path, val)
        _set_path(public, path, "" if isinstance(val, str) else None)
    return public, secrets


def load_merged_config(config_path: str, secrets_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if os.path.isfile(secrets_path):
        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = json.load(f)
        if isinstance(secrets, dict) and secrets:
            cfg = deep_merge(cfg, secrets)
    return cfg


def save_split_config(
    cfg: Dict[str, Any], config_path: str, secrets_path: str
) -> None:
    public, secrets = split_secrets(cfg)
    os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(public, f, ensure_ascii=False, indent=2)
        f.write("\n")
    prev: Dict[str, Any] = {}
    if os.path.isfile(secrets_path):
        try:
            with open(secrets_path, "r", encoding="utf-8") as f:
                prev = json.load(f) or {}
        except Exception:
            prev = {}
    merged_secrets = deep_merge(prev if isinstance(prev, dict) else {}, secrets)
    with open(secrets_path, "w", encoding="utf-8") as f:
        json.dump(merged_secrets, f, ensure_ascii=False, indent=2)
        f.write("\n")
