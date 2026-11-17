# -*- coding: utf-8 -*-
import json
import sys

import requests

ROOT_CFG = "config/config.json"
TARGET_USERID = "15192655071"
TARGET_NAME = "王志恒"


def main() -> None:
    with open(ROOT_CFG, encoding="utf-8") as f:
        w = json.load(f)["wecom"]
    token = requests.get(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": w["corp_id"], "corpsecret": w["corp_secret"]},
        timeout=20,
    ).json()["access_token"]

    cursor = ""
    hits = []
    total = 0
    while True:
        body = {"userid_list": [TARGET_USERID], "cursor": cursor, "limit": 100}
        resp = requests.post(
            "https://qyapi.weixin.qq.com/cgi-bin/externalcontact/batch/get_by_user",
            params={"access_token": token},
            json=body,
            timeout=60,
        ).json()
        if resp.get("errcode", 0) != 0:
            print("batch error", resp)
            sys.exit(1)
        batch = resp.get("external_contact_list") or []
        total += len(batch)
        for row in batch:
            ec = row.get("external_contact") or {}
            name = str(ec.get("name") or "")
            if TARGET_NAME in name:
                hits.append(
                    {
                        "external_userid": ec.get("external_userid"),
                        "name": name,
                        "type": ec.get("type"),
                        "follow_userid": (row.get("follow_info") or {}).get("userid"),
                    }
                )
                print("HIT", hits[-1], flush=True)
        cursor = resp.get("next_cursor") or ""
        print(f"scanned={total} next_cursor={bool(cursor)}", flush=True)
        if not cursor:
            break

    print("DONE hits=", hits, "total_scanned=", total, flush=True)
    with open("data/wecom_customer_hits.json", "w", encoding="utf-8") as f:
        json.dump({"userid": TARGET_USERID, "name": TARGET_NAME, "hits": hits}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
