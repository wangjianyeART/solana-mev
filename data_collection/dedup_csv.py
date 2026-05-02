"""
Clean high_volatility_minutes.csv: remove strict overlaps (when the same minute has both BTC and SOL, keep only the row with higher volatility).
"""
import csv
import os

CSV_NAME = "high_volatility_minutes.csv"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def dedup_by_minute(csv_path: str, out_path: str | None = None) -> int:
    """
    Deduplicate by datetime_utc: if the same minute has multiple rows (BTC/SOL overlap), keep only the row with the highest volatility.
    Returns the number of rows after deduplication.
    """
    out_path = out_path or csv_path
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            vol = float(row["volatility"])
            rows.append((row["datetime_utc"], vol, row))

    # Group by datetime_utc, keep only the row with the highest volatility in each group
    by_minute = {}
    for dt, vol, row in rows:
        if dt not in by_minute or vol > by_minute[dt][0]:
            by_minute[dt] = (vol, row)

    deduped = [r for _, r in by_minute.values()]
    deduped.sort(key=lambda r: float(r["volatility"]), reverse=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(deduped)

    return len(deduped)


def main():
    path = os.path.join(SCRIPT_DIR, CSV_NAME)
    if not os.path.isfile(path):
        print(f"File not found: {path}")
        return
    original_lines = sum(1 for _ in open(path, encoding="utf-8")) - 1
    n = dedup_by_minute(path)
    print(f"Cleaned {path}")
    print(f"  Original rows: {original_lines}, after dedup: {n}, removed: {original_lines - n} overlaps")
    print("  Rule: if the same minute has both BTC and SOL, keep only the row with higher volatility.")


if __name__ == "__main__":
    main()
