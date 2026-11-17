# -*- coding: utf-8 -*-
from src.filters.title_filter import (
    title_matches_keyword,
    looks_like_unwanted_gpu_listing,
    filter_items_by_keyword,
)

kw = "quadro p4000"
samples = [
    ("NVIDIA Quadro P4000 GDDR5 8GB", True, False),
    ("NVIDIA Quadro P400 2GB GDDR5", False, False),
    ("NVIDIA Quadroシリーズ用 3Dステレオブラケット 未使用品", False, True),
    ("【AI/動画編集】HP Z4 G4 Xeon 32GB P4000", True, True),
    ("Lenovo thinkpad P71", False, False),
    ("動作確認済 NVIDIA Quadro P400 2GB", False, False),
    ("NVIDIA QUADRO P4000 グラフィックボード", True, False),
    ("【中古】HP Z4G4 クリエイター向け ワークステーション NVIDIA Quadro P4000 搭載", True, True),
    ("美品 Nvidia quadroシリーズ用 3Dステレオブラケット", False, True),
]

fail = 0
for name, expect_match, expect_pc in samples:
    m = title_matches_keyword(kw, name)
    pc = looks_like_unwanted_gpu_listing(name, reference_key="gpu_quadro")
    ok = m == expect_match and pc == expect_pc
    if not ok:
        fail += 1
    print(f"{'OK' if ok else 'FAIL'} match={m}/{expect_match} pc={pc}/{expect_pc} | {name[:50]}")

items = [{"name": n, "status": "item_status_on_sale", "matched_keyword": kw} for n, _, _ in samples]
items.append({"name": "NVIDIA Quadro P4000", "status": "ITEM_STATUS_SOLD_OUT", "matched_keyword": kw})
kept, dropped = filter_items_by_keyword(kw, items, reference_key="gpu_quadro")
print(f"\nfilter kept={len(kept)} dropped={dropped}")
for it in kept:
    print(" KEEP", it["name"][:60])

raise SystemExit(fail)
