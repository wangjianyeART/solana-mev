"""
Batch-compute corresponding slot ranges for CSV files.
Based on utc_slot.py, processes random_volatility_minutes.csv and low_volatility_minutes.csv
"""
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env file not found: {env_path}")
    env = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


HELIUS_BASE = "https://mainnet.helius-rpc.com"
RATE_DELAY = 0.021
MAX_ITERS = 60


def rpc(api_key: str, method: str, params: list):
    url = f"{HELIUS_BASE}/?api-key={api_key}"
    body = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("result")


def get_block_time(api_key: str, slot: int) -> int | None:
    try:
        t = rpc(api_key, "getBlockTime", [slot])
        return int(t) if t is not None else None
    except:
        return None


def get_slot(api_key: str) -> int:
    return int(rpc(api_key, "getSlot", []))


def parse_minute_utc(s: str) -> int:
    s = s.strip().removesuffix(" UTC").strip()
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def find_slot_start(api_key: str, target_ts: int, current_max_slot: int) -> int | None:
    """Use binary search to find the starting slot closest to target_ts."""
    low = 0
    high = current_max_slot

    t_high = get_block_time(api_key, high)
    time.sleep(RATE_DELAY)
    if t_high is None or t_high < target_ts:
        return None

    best_slot = high
    for _ in range(MAX_ITERS):
        if low > high:
            break
        mid = (low + high) // 2
        t_mid = get_block_time(api_key, mid)
        time.sleep(RATE_DELAY)

        if t_mid is None:
            low = mid + 1
            continue

        if t_mid >= target_ts:
            best_slot = mid
            high = mid - 1
        else:
            low = mid + 1

    return best_slot


def process_csv(api_key: str, csv_path: str, current_max_slot: int):
    """Process a single CSV file, adding slot_start and slot_end columns."""
    if not os.path.exists(csv_path):
        print(f"  Skipped: file does not exist {csv_path}")
        return

    # Output filename: append _with_slots to the original filename
    base, ext = os.path.splitext(csv_path)
    out_path = f"{base}_with_slots{ext}"

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ["slot_start", "slot_end"]
        for r in reader:
            rows.append(r)

    print(f"  Processing {len(rows)} rows...")

    for i, r in enumerate(rows):
        start_ts = parse_minute_utc(r["datetime_utc"])
        slot_start = find_slot_start(api_key, start_ts, current_max_slot)

        if slot_start is None:
            r["slot_start"] = "N/A"
            r["slot_end"] = "N/A"
        else:
            r["slot_start"] = str(slot_start)
            r["slot_end"] = str(slot_start + 150)

        print(
            f"    [{i+1}/{len(rows)}] {r['datetime_utc']} -> [{r['slot_start']}, {r['slot_end']}]")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"  Saved to {out_path}\n")


def main():
    env = load_env()
    api_key = env.get("HELIUS_API_KEY")
    if not api_key:
        raise ValueError("HELIUS_API_KEY not found in .env")

    # List of CSV files to process
    csv_files = [
        "random_volatility_minutes.csv",
        "low_volatility_minutes.csv",
    ]

    current_max_slot = get_slot(api_key)
    time.sleep(RATE_DELAY)
    print(f"Current latest slot: {current_max_slot}\n")

    base_dir = os.path.dirname(__file__)
    for csv_file in csv_files:
        csv_path = os.path.join(base_dir, csv_file)
        print(f"Processing: {csv_file}")
        process_csv(api_key, csv_path, current_max_slot)

    print("All done!")


if __name__ == "__main__":
    main()
