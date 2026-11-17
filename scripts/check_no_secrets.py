"""提交前扫描：阻止密钥/凭据进入 Git 索引或已跟踪文件。

用法（仓库根目录）:
  python scripts/check_no_secrets.py
  python scripts/check_no_secrets.py --staged
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 文件名黑名单（相对仓库根，posix）
FORBIDDEN_NAME_SUFFIXES = (
    "/secrets.local.json",
    "/config.json",
    "/edi_credentials.json",
    "/dpop_es256_key.pem",
    "/subscriptions.json",
    "/seller_subscriptions.json",
)

# 允许出现密钥「字段名」的模板文件
ALLOWLIST_SUFFIXES = (
    "/secrets.example.json",
    "/config.example.json",
    "/edi_credentials.example.json",
    "/check_no_secrets.py",
    "/SECRETS.md",
)

# 高危字面量（真实密钥形态，而非字段名）
LITERAL_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-fA-F-]{16,}"),
    re.compile(r"-----BEGIN (?:EC|RSA|OPENSSH) PRIVATE KEY-----"),
]


def _git(args: list[str]) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        return ""
    return r.stdout


def _list_files(staged: bool) -> list[str]:
    if staged:
        out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    else:
        out = _git(["ls-files"])
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def _is_allowlisted(path: str) -> bool:
    p = path.replace("\\", "/")
    return any(p.endswith(s) or p == s.lstrip("/") for s in ALLOWLIST_SUFFIXES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--staged",
        action="store_true",
        help="只检查暂存区（适合 pre-commit）",
    )
    args = ap.parse_args()
    files = _list_files(args.staged)
    errors: list[str] = []

    for rel in files:
        low = rel.lower()
        if any(low.endswith(s) or ("/" + low).endswith(s) for s in FORBIDDEN_NAME_SUFFIXES):
            if not _is_allowlisted(rel):
                errors.append(f"[文件名] 禁止入库: {rel}")
                continue
        if _is_allowlisted(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in LITERAL_PATTERNS:
            m = pat.search(text)
            if m:
                errors.append(f"[内容] {rel}: 疑似密钥字面量 ({pat.pattern[:32]}...)")
                break

    if errors:
        print("密钥保护检查失败：", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        print(
            "\n请将密钥放在 config/secrets.local.json（已 gitignore），"
            "模板用 secrets.example.json。",
            file=sys.stderr,
        )
        return 1
    scope = "暂存区" if args.staged else "已跟踪文件"
    print(f"OK: {scope}未发现禁止入库的密钥文件/字面量（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
