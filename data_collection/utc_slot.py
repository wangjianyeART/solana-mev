import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"未找到 .env 文件: {env_path}")
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
RATE_DELAY = 0.11  # 对应 Helius 每秒约 9 次请求的频率
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
    # 移除 UTC 字样并解析
    s = s.strip().removesuffix(" UTC").strip()
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def find_slot_start(api_key: str, target_ts: int, current_max_slot: int) -> int | None:
    """用二分法寻找最接近 target_ts 的起始 Slot (左节点)"""
    low = 0
    high = current_max_slot

    # 预检：如果目标时间大于当前最高 Slot 的时间，说明数据还没产生
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
            # 遇到 Skip Slot，尝试向右探测寻找有效时间
            low = mid + 1
            continue

        if t_mid >= target_ts:
            best_slot = mid
            high = mid - 1
        else:
            low = mid + 1

    return best_slot


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
    if not os.path.exists(csv_path):
        print(f"错误: 找不到输入文件 {csv_path}")
        return

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ["slot_start", "slot_end"]
        for r in reader:
            rows.append(r)

    # 获取最新 Slot 作为搜索上限
    current_max_slot = get_slot(api_key)
    time.sleep(RATE_DELAY)
    print(f"当前最新 Slot: {current_max_slot}, 开始处理 {len(rows)} 条分钟数据...")

    for i, r in enumerate(rows):
        start_ts = parse_minute_utc(r["datetime_utc"])

        # 寻找对应的起始 Slot
        slot_start = find_slot_start(api_key, start_ts, current_max_slot)

        if slot_start is None:
            r["slot_start"] = "N/A"
            r["slot_end"] = "N/A"
        else:
            r["slot_start"] = str(slot_start)
            r["slot_end"] = str(slot_start + 150)  # 直接+150计算右节点

        print(
            f"进度: [{i+1}/{len(rows)}] {r['datetime_utc']} -> Slot Start: {r['slot_start']}")

    # 写入结果
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\n处理完成！结果已保存至: {out_path}")


if __name__ == "__main__":
    main()
