"""
Based on the existing high_volatility_minutes.csv, use binary search to find the corresponding Solana slot range for each minute.
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
RATE_DELAY = 0.11  # 尽量快
MAX_ITERS = 60
SLOT_MARGIN = 300


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


def parse_minute_utc(s: str) -> tuple[int, int]:
    s = s.strip().removesuffix(" UTC").strip()
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    return ts, ts + 59


def find_slot_min(api_key: str, target_ts: int, high_slot: int) -> int | None:
    """找最小的 slot 使得 getBlockTime(slot) >= target_ts"""
    low, high = 0, high_slot
    t_high = get_block_time(api_key, high)
    time.sleep(RATE_DELAY)
    if t_high is None or t_high < target_ts:
        return None
    for _ in range(MAX_ITERS):
        if low >= high:
            break
        mid = (low + high) // 2
        t_mid = get_block_time(api_key, mid)
        time.sleep(RATE_DELAY)
        if t_mid is None or t_mid < target_ts:
            # prune/missed slot 或时间太早，答案在右边
            low = mid + 1
        else:
            # t_mid >= target_ts，答案可能是 mid 或更左
            high = mid
    return low


def find_slot_max(api_key: str, target_ts: int, low_slot: int, high_slot: int) -> int:
    low, high = low_slot, high_slot
    t_low = get_block_time(api_key, low)
    time.sleep(RATE_DELAY)
    if t_low is None or t_low > target_ts:
        return low
    for _ in range(MAX_ITERS):
        if low >= high - 1:
            break
        mid = (low + high) // 2
        t_mid = get_block_time(api_key, mid)
        time.sleep(RATE_DELAY)
        if t_mid is None:
            high = mid
        elif t_mid <= target_ts:
            low = mid
        else:
            high = mid
    t_high = get_block_time(api_key, high)
    time.sleep(RATE_DELAY)
    return high if t_high is not None and t_high <= target_ts else low


def main():
    env = load_env()
    api_key = env.get("HELIUS_API_KEY")
    if not api_key:
        raise ValueError(".env 中未找到 HELIUS_API_KEY")

    csv_path = os.path.join(os.path.dirname(__file__),
                            "high_volatility_minutes.csv")
    out_path = os.path.join(os.path.dirname(__file__),
                            "high_volatility_minutes_with_slots.csv")

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ["slot_start", "slot_end"]
        for r in reader:
            rows.append(r)

    high_slot = get_slot(api_key)
    time.sleep(RATE_DELAY)
    print(f"开始处理 {len(rows)} 行...")

    for i, r in enumerate(rows):
        start_ts, _ = parse_minute_utc(r["datetime_utc"])
        slot_start = find_slot_min(api_key, start_ts, high_slot)
        if slot_start is None:
            r["slot_start"] = ""
            r["slot_end"] = ""
        else:
            slot_end = slot_start + 150  # 一分钟约 150 个 slot，直接加
            r["slot_start"] = str(slot_start)
            r["slot_end"] = str(slot_end)
        print(
            f"  [{i+1}/{len(rows)}] {r['datetime_utc']} -> [{r['slot_start']}, {r['slot_end']}]")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"已保存到 {out_path}")


if __name__ == "__main__":
    main()
