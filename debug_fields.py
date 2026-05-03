"""
Stampa i campi raw di 3 articoli da Vinted per capire la struttura esatta del JSON.
"""
import asyncio
import json
from src.api_client import VintedAPIClient

async def main():
    async with VintedAPIClient(concurrency=1, delay=1.0) as client:
        data = await client.get_items_page(catalog_id=4, page=1, per_page=3)
        items = data.get("items", [])
        if not items:
            print("Nessun articolo ricevuto")
            return

        raw = items[0]
        print("=== TUTTI I CAMPI DEL PRIMO ARTICOLO ===\n")
        for k, v in raw.items():
            if isinstance(v, dict):
                print(f"  {k}: (dict) {json.dumps(v, ensure_ascii=False)[:120]}")
            elif isinstance(v, list):
                print(f"  {k}: (list len={len(v)}) {json.dumps(v[:1], ensure_ascii=False)[:120]}")
            else:
                print(f"  {k}: {repr(v)}")

asyncio.run(main())
