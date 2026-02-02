from economy_index_codes import ECOS_INDEX_MAP
from bok_econ_downloader import build_specs_from_index_map, fetch_economic_indicators, to_wide

API_KEY = "PUT_YOUR_ECOS_API_KEY_HERE"

# 1) Choose which indicators to download (None -> all in the dict)
TARGETS = [
    "M2광의통화",
    "은행대출금_기업",
    "코스피지수",   # if your mapping has it
]

# 2) Optional per-series overrides (cycle/start/end/items/stat_code)
#    Example: KOSPI is usually daily (D) on many tables.
OVERRIDES = {
    # "코스피지수": {"cycle": "D", "start": "20000101", "end": "20251231"},
}

specs = build_specs_from_index_map(
    ECOS_INDEX_MAP,
    names=TARGETS,
    default_cycle="M",
    default_start="200001",
    default_end="202512",
    overrides=OVERRIDES,
)

results = fetch_economic_indicators(API_KEY, specs, fail_fast=False)

# long dict in memory
for name, df in results.items():
    print("\n===", name, "===")
    print(df.head(5))

# wide view (Date x indicators)
wide = to_wide(results)
print("\n=== WIDE PREVIEW ===")
print(wide.head(5))
