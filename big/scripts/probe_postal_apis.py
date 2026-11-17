"""仅 HTTP 探测：日本邮政 / 追踪なう / 其它，不启浏览器、不访问 jpgoodbuy。"""

from __future__ import annotations

import json
import sys

import requests

TID = sys.argv[1] if len(sys.argv) > 1 else "CN135767762JP"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
KWS = ["等待用户缴税", "提交申报材料", "您的邮件已", "派送", "投柜", "海关", "通关"]


def dump(name: str, r: requests.Response) -> None:
    raw = r.content
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("utf-8", "ignore")
    hits = [k for k in KWS if k in text]
    print(f"\n=== {name} {r.status_code} len={len(text)} hits={hits}")
    print("ct:", r.headers.get("content-type", ""))
    if "json" in (r.headers.get("content-type") or "").lower() or text[:1] in "{[":
        try:
            print(json.dumps(json.loads(text), ensure_ascii=False, indent=2)[:2000])
            return
        except Exception:
            pass
    print(text[:800].replace("\n", " | "))


def main() -> None:
    # 1) 追跡なう（免费无密钥，日本郵便）
    for carrier in ("post", "japanpost", "auto", ""):
        q = f"?carrier={carrier}" if carrier else ""
        url = f"https://tsuiseki-now.ogatomo.net/api/json/{TID}{q}"
        try:
            dump(f"tsuiseki carrier={carrier or 'default'}", requests.get(url, timeout=25, headers=UA))
        except Exception as e:
            print("ERR tsuiseki", carrier, e)

    # 2) 日本邮政官方 direct 查询
    try:
        dump(
            "japanpost_direct",
            requests.get(
                "https://trackings.post.japanpost.jp/services/srv/search/direct",
                params={"searchKind": "S002", "locale": "ja", "reqCodeNo1": TID},
                timeout=25,
                headers=UA,
            ),
        )
    except Exception as e:
        print("ERR japanpost", e)

    # 3) 日本邮政英文
    try:
        dump(
            "japanpost_en",
            requests.get(
                "https://trackings.post.japanpost.jp/services/srv/search/direct",
                params={"searchKind": "S002", "locale": "en", "reqCodeNo1": TID},
                timeout=25,
                headers=UA,
            ),
        )
    except Exception as e:
        print("ERR japanpost_en", e)

    # 4) 中国邮政 11183 公开查询试探
    for url, params in [
        ("https://www.11183.com.cn/pc-web/query/queryTrace", {"mailNo": TID}),
        ("http://www.ems.com.cn/emsapi/ems/queryTrace", {"mailNum": TID}),
        ("https://yjcx.chinapost.com.cn/qps/yjcx", None),
    ]:
        try:
            dump(url, requests.get(url, params=params, timeout=20, headers=UA))
        except Exception as e:
            print("ERR", url, e)


if __name__ == "__main__":
    main()
