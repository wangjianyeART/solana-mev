"""
03_fetch_bundles.py
For every signature in arbs_*.json, query the Jito bundle API and save
the response to bundles_*.json. Existing files are skipped (resumable).

Usage: python 03_fetch_bundles.py
"""

import json
import asyncio
import aiohttp
import time
import glob
from pathlib import Path

BASE_URL    = "https://bundles.jito.wtf/api/v1/bundles"
ARBS_DIR    = Path("jup_arb_data_cursor_std")
OUT_DIR     = Path("jito_bundle_results")
CONCURRENCY = 2       # low concurrency to avoid Cloudflare / 429
RETRY_WAIT  = 8.0
MAX_RETRIES = 5
REQ_DELAY   = 0.2     # minimum delay after each successful request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://jito.wtf/",
}

OUT_DIR.mkdir(exist_ok=True)


async def fetch_with_retry(session: aiohttp.ClientSession, url: str):
    """Return JSON body; False on 404; None on failure or rate-limit (so we
    can distinguish "confirmed not in a bundle" from "lookup failed")."""
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    await asyncio.sleep(REQ_DELAY)
                    return await resp.json()
                elif resp.status == 404:
                    return False                  # confirmed not present
                elif resp.status in (429, 403):
                    wait = RETRY_WAIT * (attempt + 1)
                    await asyncio.sleep(wait)     # rate-limit / Cloudflare: wait & retry
                else:
                    return None                   # other errors: mark as unknown
        except Exception:
            await asyncio.sleep(3)
    return None                                   # all retries failed: unknown


async def lookup(session: aiohttp.ClientSession, sig: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        result = {
            "signature":  sig,
            "in_bundle":  None,    # default None (unknown); failed lookups stay None, never falsely False
            "bundle_id":  None,
            "bundle":     None,
            "queried_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
        tx_data = await fetch_with_retry(session, f"{BASE_URL}/transaction/{sig}")
        if tx_data is False:
            result["in_bundle"] = False   # 404: confirmed not in a bundle
            return result
        if tx_data is None:
            return result                 # lookup failed: keep None (unknown)

        if isinstance(tx_data, list):
            tx_data = tx_data[0] if tx_data else None
        if not tx_data:
            result["in_bundle"] = False
            return result

        bundle_id = tx_data.get("bundle_id")
        if not bundle_id:
            result["in_bundle"] = False
            return result

        result["in_bundle"] = True
        result["bundle_id"] = bundle_id
        bundle_data = await fetch_with_retry(session, f"{BASE_URL}/bundle/{bundle_id}")
        if bundle_data and bundle_data is not False:
            if isinstance(bundle_data, list):
                bundle_data = bundle_data[0] if bundle_data else None
            result["bundle"] = bundle_data
        return result


async def process_file(session: aiohttp.ClientSession, arbs_file: Path, sem: asyncio.Semaphore):
    # arbs_0006.json → bundles_0006.json
    out_file = OUT_DIR / arbs_file.name.replace("arbs_", "bundles_")

    if out_file.exists():
        data = json.loads(out_file.read_text())
        in_b = sum(1 for r in data if r["in_bundle"])
        print(f"  [skip] {arbs_file.name} → {out_file.name} already exists (in_bundle={in_b}/{len(data)})")
        return

    sigs = [a["signature"] for a in json.loads(arbs_file.read_text())]
    print(f"  processing {arbs_file.name}  ({len(sigs)} records)...")

    tasks = [lookup(session, sig, sem) for sig in sigs]
    results = await asyncio.gather(*tasks)

    in_bundle = sum(1 for r in results if r["in_bundle"])
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"  done {arbs_file.name} → {out_file.name}  in_bundle={in_bundle}/{len(sigs)}  ({in_bundle/len(sigs)*100:.1f}%)")


async def main():
    files = sorted(ARBS_DIR.glob("arbs_*.json"))
    print(f"Found {len(files)} arbs files; existing outputs are skipped.\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for f in files:
            await process_file(session, f, sem)

    # summary
    print("\n─── summary ───")
    total = in_b = 0
    for bf in sorted(OUT_DIR.glob("bundles_*.json")):
        data = json.loads(bf.read_text())
        n = len(data)
        b = sum(1 for r in data if r["in_bundle"])
        total += n
        in_b  += b
        print(f"  {bf.name}  {b:>5}/{n}  ({b/n*100:.1f}%)")
    print(f"\n  total: {in_b}/{total}  ({in_b/total*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
