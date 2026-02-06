#!/usr/bin/env python3
"""
Find Wormhole Portal USDC records within a given date where the transfer goes
from Solana to Ethereum and the target chain status is successful.
Paginates through the API (PAGE_SIZE per page) to pull all matching records for the day.

Wormhole chain IDs (official): 1=Solana, 2=Ethereum, 5=Polygon, etc.
  https://wormhole.com/docs/products/reference/chain-ids/

Usage:
  python find_first_usdc_to_eth.py                    # default: today
  python find_first_usdc_to_eth.py 2025-08-12         # specific date

Output:
  wormhole_solana_to_eth_all_YYYY-MM-DD.json  All fetched Solana->ETH records (raw data)
  first_usdc_to_eth.json                      First USDC completed record of the day
  all_usdc_solana_to_eth_YYYY-MM-DD.json      All USDC completed records of the day

Dependencies: pip install requests
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

API_URL = "https://api.wormholescan.io/api/v1/operations"
APP_ID = "MAYAN_SWIFT"
# Wormhole chainId (official): 1=Solana, 2=Ethereum
SOURCE_CHAIN_SOLANA = 1
TARGET_CHAIN_ETH = 2
# Records per request: 1=one per request (many requests), 100=up to 100 per request (fewer requests)
# API: pageSize=1 returns 1, pageSize=100 returns up to 100; paginate with page=0,1,2,...
PAGE_SIZE = 100
# Maximum number of pages to prevent infinite loops on errors; 0 means no limit
MAX_PAGES = 1


def day_range(date_str: str):
    """Given a date YYYY-MM-DD, return the range from 00:00:00Z to next day 00:00:00Z."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    from_ts = dt.strftime("%Y-%m-%dT00:00:00.000Z")
    to_dt = dt + timedelta(days=1)
    to_ts = to_dt.strftime("%Y-%m-%dT00:00:00.000Z")
    return from_ts, to_ts


def is_successful_operation(op: dict) -> bool:
    """Target chain must be completed; source chain is typically confirmed."""
    tgt = op.get("targetChain") or {}
    src = op.get("sourceChain") or {}
    tgt_status = (tgt.get("status") or "").lower()
    src_status = (src.get("status") or "").lower()
    if tgt_status != "completed":
        return False
    return src_status in ("confirmed", "completed")


def is_usdc_operation(op: dict) -> bool:
    """Check whether the bridged asset from Solana is USDC (checks data.symbol)."""
    data = op.get("data") or {}
    if (data.get("symbol") or "").upper() == "USDC":
        return True
    if (data.get("tokenSymbol") or "").upper() == "USDC":
        return True
    return False


def fetch_all_usdc_solana_to_eth(date_str: str):
    """
    For a given day's time range, paginate through all Solana -> ETH records,
    then locally filter for USDC with completed status.
    Returns (list of matching USDC ops, first USDC op or None, all fetched operations).
    """
    from_ts, to_ts = day_range(date_str)
    print(f"Date: {date_str}")
    print(f"   Time range: {from_ts} ~ {to_ts}")
    print(f"   Source chain: Solana (chainId=1) -> Target chain: Ethereum (chainId=2)")
    print(f"   Pagination: pageSize={PAGE_SIZE}, fetching pages until no more data\n")

    # Paginate and fetch all records
    all_ops = []
    page = 0
    while True:
        if MAX_PAGES and page >= MAX_PAGES:
            print(f"   Reached max pages {MAX_PAGES}, stopping.")
            break
        params = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "sortOrder": "ASC",
            # "appId": APP_ID,
            "sourceChain": SOURCE_CHAIN_SOLANA,
            "targetChain": TARGET_CHAIN_ETH,
            "from": from_ts,
            "to": to_ts,
        }
        try:
            r = requests.get(API_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"ERROR: Request failed (page={page}): {e}")
            break

        ops = data.get("operations") or []
        if not ops:
            if page == 0:
                print("WARNING: No Solana -> ETH Portal records found for this date.")
            break

        all_ops.extend(ops)
        print(f"   page {page}: {len(ops)} records this page, {len(all_ops)} total so far")
        page += 1
        if len(ops) < PAGE_SIZE:
            break

    # Filter fetched results for USDC with completed status
    all_matches = []
    for op in all_ops:
        if not is_usdc_operation(op):
            continue
        if not is_successful_operation(op):
            continue
        all_matches.append(op)

    print(
        f"\n   Fetched {len(all_ops)} Solana->ETH records; USDC and completed: {len(all_matches)}\n")
    if all_ops and not all_matches:
        print("   [debug] First 5 records - symbol / targetChain.status:")
        for i, op in enumerate(all_ops[:5]):
            d = op.get("data") or {}
            tgt = op.get("targetChain") or {}
            sym = d.get("symbol") or d.get("tokenSymbol") or "-"
            st = tgt.get("status") or "(none)"
            print(f"      {i+1}. symbol={sym}  targetChain.status={st}")
        print()
    first = all_matches[0] if all_matches else None
    return all_matches, first, all_ops


def fetch_first_usdc_to_eth(date_str: str):
    """Backward-compatible: returns only the first match. Internally paginates then takes the first."""
    all_matches, first, _ = fetch_all_usdc_solana_to_eth(date_str)
    if first:
        print("OK: Found the first successful USDC record (Solana -> ETH) for this day:\n")
    elif not all_matches:
        print("WARNING: No USDC found among successful Solana -> ETH records for this date.")
    return first


def print_operation(op: dict):
    """Print key information for a single operation."""
    if not op:
        return
    content = op.get("content") or {}
    std = content.get("standarizedProperties") or {}
    src = op.get("sourceChain") or {}
    tgt = op.get("targetChain") or {}
    data = op.get("data") or {}

    print("  id:", op.get("id"))
    print("  symbol:", data.get("symbol"))
    print("  tokenAmount:", data.get("tokenAmount"))
    print("  usdAmount:", data.get("usdAmount"))
    print("  fromChain:", std.get("fromChain"),
          "-> toChain:", std.get("toChain"))
    print("  toAddress:", std.get("toAddress"))
    print("  sourceChain.status:", src.get("status"))
    print("  sourceChain.timestamp:", src.get("timestamp"))
    print("  sourceChain.txHash:", src.get("transaction", {}).get("txHash"))
    print("  targetChain.status:", tgt.get("status"))
    print("  targetChain.timestamp:", tgt.get("timestamp"))
    print("  targetChain.txHash:", tgt.get("transaction", {}).get("txHash"))
    print()
    print("  Full JSON written to: first_usdc_to_eth.json")


def main():
    if len(sys.argv) >= 2:
        date_str = sys.argv[1].strip()
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    dir_path = os.path.dirname(__file__)
    all_matches, first, all_ops = fetch_all_usdc_solana_to_eth(date_str)

    print(f"\nFound {len(all_matches)} matching USDC records (Solana -> ETH, completed)\n")

    # Save all fetched data locally
    out_all_raw = os.path.join(
        dir_path, f"wormhole_solana_to_eth_all_{date_str}.json")
    with open(out_all_raw, "w", encoding="utf-8") as f:
        json.dump(
            {"date": date_str, "total": len(all_ops), "operations": all_ops},
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(
        f"  All {len(all_ops)} fetched records written to: wormhole_solana_to_eth_all_{date_str}.json")

    if first:
        print_operation(first)
        out_first = os.path.join(dir_path, "first_usdc_to_eth.json")
        with open(out_first, "w", encoding="utf-8") as f:
            json.dump(first, f, indent=2, ensure_ascii=False)

    if all_matches:
        out_all = os.path.join(
            dir_path, f"all_usdc_solana_to_eth_{date_str}.json")
        with open(out_all, "w", encoding="utf-8") as f:
            json.dump(
                {"date": date_str, "total": len(
                    all_matches), "operations": all_matches},
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(
            f"  {len(all_matches)} matching USDC records written to: all_usdc_solana_to_eth_{date_str}.json")


if __name__ == "__main__":
    main()
