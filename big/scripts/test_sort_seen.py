# -*- coding: utf-8 -*-
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import settings
from src.pipeline import processor, state
from src.sources import mercari, paypay, yahoo_auction


def main() -> None:
    items = yahoo_auction.search({"keyword": "quadro p4000"})
    print(
        "yahoo",
        len(items),
        "sort",
        settings.YAHOO_AUCTION.get("sort"),
        "flea_left",
        any((i.get("raw") or {}).get("isFleamarketItem") for i in items),
    )

    p = paypay.search({"keyword": "quadro p2000"})
    print(
        "paypay",
        len(p),
        "first",
        p[0].get("created") if p else None,
        "last",
        p[-1].get("created") if p else None,
    )

    m = mercari.search({"keyword": "quadro p2200"})
    print(
        "mercari",
        len(m),
        "first_created",
        m[0].get("created") if m else None,
        "second_created",
        m[1].get("created") if len(m) > 1 else None,
    )

    seen = state.load_seen()
    subs = processor.load_subscriptions()
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "reports", "*.json")))[-6:]:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
        sub = next((s for s in subs if s.get("sub_user_id") == rep.get("sub_user_id")), None)
        if not sub:
            continue
        key = processor._sub_key(sub)
        passed = rep.get("passed") or []
        pushed = {f"{x.get('source_site')}:{x.get('id')}" for x in passed}
        state.mark_seen(key, passed, seen_map=seen, pushed_ids=pushed)
    state.save_seen(seen)
    print("seen keys", list(seen.keys()))
    for k, v in seen.items():
        meta = v.get("meta") or {}
        print(
            " ",
            k[:60],
            "ids",
            len(v.get("ids") or []),
            "pushed",
            sum(1 for m in meta.values() if m.get("pushed")),
        )

    # 去重验证：用已见 id 再 filter 应为 0
    if subs and seen:
        sub = dict(subs[0])
        key = processor._sub_key(sub)
        fake = [{"source_site": "mercari", "id": "dummy"}]
        # take one real id if any
        ids = (seen.get(key) or {}).get("ids") or []
        if ids:
            site, iid = ids[0].split(":", 1)
            fake = [{"source_site": site, "id": iid, "name": "x"}]
            left = state.filter_unseen(key, fake, seen_map=seen)
            print("dedupe_check leftover", len(left), "(expect 0)")


if __name__ == "__main__":
    main()
