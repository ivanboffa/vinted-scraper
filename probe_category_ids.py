"""
Probe Vinted.it to find all valid clothing category IDs.
Tests candidate IDs and reports which ones return actual items.
"""
import asyncio, sys, json, time
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient

# Known working IDs for reference
KNOWN = {
    4: "donna/vestiti", 1206: "donna/jeans", 3: "donna/top-maglie",
    5: "donna/giacche-cappotti", 16: "donna/scarpe", 19: "donna/borse",
    18: "donna/accessori", 1187: "donna/abbigliamento-sportivo",
    1189: "donna/costumi-da-bagno", 1190: "donna/intimo-pigiami", 2: "donna/abbigliamento",
    12: "uomo/abbigliamento", 1258: "uomo/t-shirt-polo", 1261: "uomo/giacche-cappotti",
    1262: "uomo/pantaloni", 1263: "uomo/jeans", 1264: "uomo/shorts",
    13: "uomo/scarpe", 14: "uomo/borse-zaini", 15: "uomo/accessori",
    1188: "uomo/abbigliamento-sportivo", 1265: "uomo/intimo-pigiami",
    1266: "uomo/costumi-da-bagno", 1267: "uomo/abbigliamento-formale",
}

# Candidate ID ranges to probe
CANDIDATES = (
    list(range(1, 25)),           # Low IDs (main categories)
    list(range(1180, 1215)),      # Around known donna subcategories
    list(range(1250, 1290)),      # Around known uomo subcategories
    list(range(1290, 1350)),      # Possible extensions
    list(range(1350, 1420)),      # More extensions
    list(range(1600, 1650)),      # Higher range
    list(range(1900, 1950)),      # Even higher
    list(range(2100, 2150)),      # Try 2000s
)

ALL_CANDIDATES = sorted(set(
    cid for r in CANDIDATES for cid in r if cid not in KNOWN
))

print(f"Testing {len(ALL_CANDIDATES)} candidate IDs...", flush=True)
print(f"(known IDs skipped: {sorted(KNOWN.keys())})", flush=True)

found: list[tuple[int, int]] = []  # (id, item_count)


async def probe_one(client, cid: int, semaphore: asyncio.Semaphore) -> tuple[int, int]:
    async with semaphore:
        await asyncio.sleep(0.3)
        data = await client.get_items_page(cid, page=1, per_page=10, retries=1)
        if not data:
            return cid, 0
        items = data.get("items", [])
        pagination = data.get("pagination", {})
        total = pagination.get("total_items", len(items))
        return cid, total


async def main():
    async with VintedAPIClient(concurrency=4, delay=0.3) as client:
        semaphore = asyncio.Semaphore(4)
        t0 = time.monotonic()

        tasks = [probe_one(client, cid, semaphore) for cid in ALL_CANDIDATES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.monotonic() - t0
        print(f"\nDone in {elapsed:.0f}s", flush=True)

    print(f"\n{'='*60}", flush=True)
    print("VALID IDs (returning items):", flush=True)
    print(f"{'='*60}", flush=True)

    valid = []
    for r in results:
        if isinstance(r, Exception):
            continue
        cid, count = r
        if count > 0:
            valid.append((cid, count))
            print(f"  ID {cid:6d}: {count:,} items", flush=True)

    print(f"\nTotal valid new IDs: {len(valid)}", flush=True)
    print("\nAlso add these to KNOWN (combined list):", flush=True)
    all_valid = sorted([(cid, KNOWN.get(cid, "UNKNOWN"), ct)
                        for cid, ct in [(cid, ct) for cid, ct in valid]] +
                       [(cid, name, -1) for cid, name in KNOWN.items()])
    for cid, name, ct in sorted(all_valid, key=lambda x: x[0]):
        label = name if ct == -1 else f"NEW ({ct:,} items)"
        print(f"    ({cid:6d}, \"{label}\"),", flush=True)

    # Save results
    with open("data/probed_ids.json", "w", encoding="utf-8") as f:
        json.dump({"known": KNOWN, "new_valid": {str(cid): ct for cid, ct in valid}},
                  f, ensure_ascii=False, indent=2)
    print("\nSaved to data/probed_ids.json", flush=True)


asyncio.run(main())
