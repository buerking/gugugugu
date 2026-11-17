"""探测可用物流轨迹接口（不依赖 17track）。"""

from __future__ import annotations

import re
import sys

import requests

TID = sys.argv[1] if len(sys.argv) > 1 else "CN135767762JP"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    )
}
KWS = ["等待用户缴税", "提交申报材料", "您的邮件已", "派送", "已投柜"]


def show(name: str, r: requests.Response) -> None:
    text = r.content.decode("utf-8", "ignore")
    hits = [k for k in KWS if k in text]
    print(f"\n=== {name} status={r.status_code} len={len(text)} hits={hits}")
    print("ct:", r.headers.get("content-type", ""))
    # 打印含关键词的行
    for line in re.findall(r">([^<]{2,120})<", text):
        s = " ".join(line.split())
        if any(k in s for k in KWS + ["运单号", "物流", "海关", "投递"]):
            print(" ", s)


def main() -> None:
    # 1) 官网静态轨迹页（之前已验证可用）
    url = f"https://www.jpgoodbuy.com/package_track-{TID}.html"
    r = requests.get(url, timeout=25, headers=UA)
    show("jpgoodbuy_html", r)

    # 从页面里抠可能的 ajax
    html = r.content.decode("utf-8", "ignore")
    for m in sorted(set(re.findall(r"['\"]([^'\"]*(?:package_track|query_order|ajax)[^'\"]*)['\"]", html, re.I))):
        print(" found:", m[:160])

    # 2) package_track.php 几种常见参数
    ajax_headers = {**UA, "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.jpgoodbuy.com/track"}
    trials = [
        ("GET", "https://www.jpgoodbuy.com/package_track.php", {"act": "query_order", "order_sn": TID}),
        ("GET", "https://www.jpgoodbuy.com/package_track.php", {"act": "query", "number": TID}),
        ("POST", "https://www.jpgoodbuy.com/package_track.php?act=query_order", {"order_sn": TID}),
        ("POST", "https://www.jpgoodbuy.com/package_track.php", {"act": "query_order", "order_sn": TID}),
        ("POST", "https://www.jpgoodbuy.com/package_track.php", {"act": "ajax_query", "number": TID}),
        ("GET", f"https://track.fba.jp/yii/web/index.php?r=home/track/index&number={TID}", None),
    ]
    for method, u, data in trials:
        try:
            if method == "GET":
                rr = requests.get(u, params=data, timeout=20, headers=ajax_headers)
            else:
                rr = requests.post(u, data=data or {}, timeout=20, headers=ajax_headers)
            show(f"{method} {u}", rr)
        except Exception as e:
            print("ERR", method, u, e)

    # 3) 日本邮政公开查询页（HTML，非官方 JSON API）
    try:
        jp = requests.get(
            "https://trackings.post.japanpost.jp/services/srv/search/direct",
            params={"searchKind": "S002", "locale": "ja", "reqCodeNo1": TID},
            timeout=25,
            headers=UA,
        )
        show("japanpost_direct", jp)
    except Exception as e:
        print("ERR japanpost", e)

    # 4) 中国邮政 11183 常见接口试探（可能失效）
    try:
        cp = requests.get(
            "https://www.11183.com.cn/pc-web/query/queryTrace",
            params={"mailNo": TID},
            timeout=20,
            headers=UA,
        )
        show("china_post_11183", cp)
    except Exception as e:
        print("ERR 11183", e)


if __name__ == "__main__":
    main()
