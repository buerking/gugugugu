# -*- coding: utf-8 -*-
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.sources import yahoo_auction

BASE = {
    "offset": 0,
    "limit": 24,
    "sort": "-FIRST_START_TIME",
    "isFinishToday": False,
    "isAuctionSubmit": False,
    "useSpeller": True,
    "src": "pc_result_open_clnht",
    "prefectureCodes": [],
    "exceptPrefectureCodes": [],
    "conditions": [],
    "sellerTypes": [],
    "exceptCategoryIds": [],
    "brandIds": [],
    "aucUserIds": [],
    "submitType": "ALL",
    "shpSpec": [],
    "query": "iphone",
    "categoryIds": [0],
    "imageSize": "SMALL",
    "imageShape": "RAW",
    "destinationPrefectureCode": "13",
    "status": "OPEN",
    "rewriteQueryTypes": ["WAND_SEARCH"],
}


def run(label: str, patch: dict) -> None:
    body = copy.deepcopy(BASE)
    body.update(patch)
    try:
        data = yahoo_auction._curl_json(
            yahoo_auction.SEARCH_URL,
            yahoo_auction._headers(""),
            json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        print(f"{label}: ERROR {exc}")
        return
    err = data.get("error")
    if err:
        msg = json.dumps(err, ensure_ascii=False)
        print(f"{label}: API_ERROR {msg[:260]}")
        return
    items = data.get("items") or []
    flea = sum(1 for it in items if yahoo_auction._is_fleamarket_item(it))
    total = data.get("totalResultsAvailable")
    print(f"{label}: total={total} n={len(items)} flea={flea} auction={len(items) - flea}")


def main() -> None:
    run("baseline submitType=ALL", {})

    for st in (
        ["AUCTION"],
        ["STORE"],
        ["PERSONAL"],
        ["FLEAMARKET"],
        ["AUCTION", "STORE"],
        ["AUCTION", "PERSONAL"],
        ["STORE", "PERSONAL"],
    ):
        run(f"sellerTypes={st}", {"sellerTypes": st})

    for sub in (
        "AUCTION",
        "AUC",
        "STORE",
        "FLEA",
        "FLEAMARKET",
        "FIXED",
        "BID",
        "NORMAL",
        "ITEM",
        "SHOPPING",
    ):
        run(f"submitType={sub}", {"submitType": sub})

    for key, val in [
        ("isFleamarket", False),
        ("isFleamarketItem", False),
        ("excludeFleamarket", True),
        ("withFleamarket", False),
        ("includeFleamarket", False),
        ("fleamarket", False),
        ("isAuctionOnly", True),
        ("auctionOnly", True),
        ("isPaypayFleamarket", False),
        ("excludePaypayFleamarket", True),
        ("isShopping", False),
        ("isShoppingItem", False),
        ("withAuction", True),
        ("auction", True),
    ]:
        run(f"{key}={val}", {key: val})

    for key, val in [
        ("exceptSellerTypes", ["FLEAMARKET"]),
        ("excludeSellerTypes", ["FLEAMARKET"]),
        ("itemTypes", ["AUCTION"]),
        ("productType", "AUCTION"),
        ("salesMethod", "AUCTION"),
        ("platform", "AUCTION"),
    ]:
        run(f"{key}={val}", {key: val})

    # conditions facet style guesses
    for cond in (
        [{"key": "isFleamarketItem", "value": "false"}],
        [{"facetId": "isFleamarketItem", "value": False}],
        [{"name": "sellerType", "value": "AUCTION"}],
    ):
        run(f"conditions={cond}", {"conditions": cond})


if __name__ == "__main__":
    main()
