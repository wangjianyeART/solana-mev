import csv
import os
import urllib.request
import json
import time
from datetime import datetime, timezone

BINANCE = "https://api.binance.com/api/v3/klines"
RATE_DELAY = 0.3
TWO_YEARS_MS = 2 * 365 * 24 * 60 * 60 * 1000
TOP_DAYS = 30      # First select the 30 days with highest volatility
TOP_MINUTES = 60   # Then select the 60 minutes with highest volatility from those days


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000):
    """Fetch klines with pagination. Daily klines fit in one request; 1m klines paginate by 1000."""
    out = []
    start = start_ms
    while start < end_ms:
        url = f"{BINANCE}?symbol={symbol}&interval={interval}&startTime={start}&endTime={end_ms}&limit={limit}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if not data:
            break
        out.extend(data)
        start = data[-1][0] + (60000 if interval == "1m" else 86400000)
        time.sleep(RATE_DELAY)
    return out


def daily_volatility(candle):
    ot, o, h, l = candle[0], float(candle[1]), float(
        candle[2]), float(candle[3])
    return (ot, (h - l) / o if o > 0 else 0.0)


def minute_volatility(candle):
    """Return (open_time, volatility, open, high, low, close, volume). Binance kline[5] is the trading volume."""
    ot, o, h, l, c = candle[0], float(candle[1]), float(
        candle[2]), float(candle[3]), float(candle[4])
    vol_quote = float(candle[5])  # Trading volume (in quote currency, e.g. USDT)
    return (ot, (h - l) / o if o > 0 else 0.0, o, h, l, c, vol_quote)


def top_volatile_minutes_two_stage(symbol: str, top_days: int = TOP_DAYS, top_minutes: int = TOP_MINUTES):
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - TWO_YEARS_MS

    # 1) Daily klines: select the top_days days with highest volatility
    daily = fetch_klines(symbol, "1d", start_ms, end_ms)
    daily_with_vol = [daily_volatility(c) for c in daily]
    daily_with_vol.sort(key=lambda x: x[1], reverse=True)
    selected_days = [x[0] for x in daily_with_vol[:top_days]]

    # 2) For each selected day, fetch 1m klines, compute minute volatility, then select top_minutes
    all_minutes = []
    for day_start_ms in selected_days:
        day_end_ms = day_start_ms + 24 * 60 * 60 * 1000
        minutes = fetch_klines(
            symbol, "1m", day_start_ms, day_end_ms, limit=1000)
        for m in minutes:
            all_minutes.append(minute_volatility(m))
        # If a day has >1000 candles, the fetch above already paginates to pull all of them
    # If fetch_klines fully paginates 1m within [day_start, day_end], the above is sufficient; otherwise add while-loop pagination in fetch for 1m

    all_minutes.sort(key=lambda x: x[1], reverse=True)
    return all_minutes[:top_minutes]  # 每项 (ot, vol, o, h, l, c, volume)


def main():
    # Get high-volatility minutes for BTC and SOL separately, then take their union
    rows_for_csv = []
    for symbol in ["BTCUSDT", "SOLUSDT"]:
        top = top_volatile_minutes_two_stage(symbol, TOP_DAYS, TOP_MINUTES)
        print(f"\n{symbol} top {TOP_MINUTES} most volatile minutes within the {TOP_DAYS} most volatile days:")
        for t in top:
            ot, vol, o, h, l, c, volume = t[0], t[1], t[2], t[3], t[4], t[5], t[6]
            ts = datetime.utcfromtimestamp(
                ot / 1000).strftime("%Y-%m-%d %H:%M UTC")
            print(
                f"  {ts}  vol={vol:.4f}  O={o} H={h} L={l} C={c}  volume={volume:.2f}")
            rows_for_csv.append((symbol, ts, vol, o, h, l, c, volume))

    # Sort union by volatility descending
    rows_for_csv.sort(key=lambda r: r[2], reverse=True)

    # Deduplicate: for the same datetime_utc, keep only the entry with higher volatility (already sorted, so keep first occurrence)
    seen_ts = set()
    deduped = []
    for r in rows_for_csv:
        ts = r[1]
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        deduped.append(r)

    # Save as CSV (in the script's directory)
    out_path = os.path.join(os.path.dirname(__file__),
                            "high_volatility_minutes.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "datetime_utc", "volatility",
                   "open", "high", "low", "close", "volume"])
        w.writerows(deduped)
    print(
        f"\nSaved union results to {out_path}, {len(rows_for_csv)} rows before dedup, {len(deduped)} rows after dedup.")


if __name__ == "__main__":
    main()
