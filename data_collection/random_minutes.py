"""
随机从过去2年内选择 BTC 和 SOL 各 60 分钟，获取波动率、交易量等信息。
输出 CSV 列与 high_volatility_minutes.csv 对齐。
"""
import csv
import os
import random
import urllib.request
import json
import time
from datetime import datetime, timezone

BINANCE = "https://api.binance.com/api/v3/klines"
RATE_DELAY = 0.1
TWO_YEARS_MS = 2 * 365 * 24 * 60 * 60 * 1000
RANDOM_MINUTES = 60  # 每个标的随机选 60 分钟


def fetch_single_kline(symbol: str, start_ms: int) -> dict | None:
    """获取某一分钟的 K 线数据"""
    url = f"{BINANCE}?symbol={symbol}&interval=1m&startTime={start_ms}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if data and len(data) > 0:
            candle = data[0]
            ot = candle[0]
            o, h, l, c = float(candle[1]), float(
                candle[2]), float(candle[3]), float(candle[4])
            volume = float(candle[5])
            volatility = (h - l) / o if o > 0 else 0.0
            return {
                "open_time": ot,
                "volatility": volatility,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": volume,
            }
    except Exception as e:
        print(f"  获取 {symbol} {start_ms} 失败: {e}")
    return None


def generate_random_timestamps(count: int, start_ms: int, end_ms: int) -> list[int]:
    """生成 count 个不重复的随机分钟时间戳（毫秒，对齐到分钟）"""
    all_minutes = set()
    while len(all_minutes) < count:
        ts = random.randint(start_ms, end_ms)
        ts = ts // 60000 * 60000  # 对齐到分钟
        all_minutes.add(ts)
    return list(all_minutes)


def main():
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - TWO_YEARS_MS

    rows_for_csv = []

    for symbol in ["BTCUSDT", "SOLUSDT"]:
        print(f"正在为 {symbol} 随机选择 {RANDOM_MINUTES} 分钟...")
        random_times = generate_random_timestamps(
            RANDOM_MINUTES, start_ms, end_ms)

        for i, ts in enumerate(random_times):
            kline = fetch_single_kline(symbol, ts)
            time.sleep(RATE_DELAY)
            if kline:
                dt_str = datetime.utcfromtimestamp(
                    kline["open_time"] / 1000).strftime("%Y-%m-%d %H:%M UTC")
                rows_for_csv.append((
                    symbol,
                    dt_str,
                    kline["volatility"],
                    kline["open"],
                    kline["high"],
                    kline["low"],
                    kline["close"],
                    kline["volume"],
                ))
            if (i + 1) % 20 == 0:
                print(f"  {symbol}: {i+1}/{RANDOM_MINUTES} 完成")

    # 按波动率降序排序
    rows_for_csv.sort(key=lambda r: r[2], reverse=True)

    # 保存 CSV
    out_path = os.path.join(os.path.dirname(__file__),
                            "random_volatility_minutes.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "datetime_utc", "volatility",
                   "open", "high", "low", "close", "volume"])
        w.writerows(rows_for_csv)

    print(f"\n已保存到 {out_path}，共 {len(rows_for_csv)} 条。")


if __name__ == "__main__":
    main()
