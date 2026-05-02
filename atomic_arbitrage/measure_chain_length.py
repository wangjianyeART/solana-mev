"""
交易链长度检测：遍历 data_collection/data 下所有交易，按 log 中 transfer 出现次数/2 计算链长度。
输出每笔交易的 (sig, slot, chain_length)，用于与套利利润做对照或全量分布分析。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(line_buffering=True)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data_collection", "data")
OUTPUT_JSON = os.path.join(os.path.dirname(
    __file__), "chain_length_by_tx.json")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "chain_length_by_tx.csv")


def get_chain_length(tx):
    """log 中 transfer 相关出现次数 / 2 下取整。"""
    logs = tx.get("logs") or tx.get("logMessages") or []
    transfer_count = sum(
        1 for line in logs if "transfer" in (line or "").lower())
    return transfer_count // 2


def extract_slot_range(filename):
    match = re.search(r"mev_(\d+)_(\d+)\.json", filename)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return filename


def main():
    if not os.path.exists(DATA_DIR):
        print(f"错误: 找不到数据目录 {DATA_DIR}")
        return

    json_files = [f for f in os.listdir(DATA_DIR) if f.endswith(
        ".json") and not f.startswith("._")]
    json_files.sort()
    print(f"共找到 {len(json_files)} 个 JSON 文件\n")

    rows = []

    for i, filename in enumerate(json_files):
        file_path = os.path.join(DATA_DIR, filename)
        slot_range = extract_slot_range(filename)

        with open(file_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"[{i+1}/{len(json_files)}] 解析错误 {filename}: {e}", flush=True)
                continue

        n_tx = 0
        for slot_id, transactions in data.items():
            for tx in transactions:
                sig = tx.get("sig") or ""
                chain_len = get_chain_length(tx)
                rows.append({
                    "slot_range": slot_range,
                    "slot": int(slot_id) if isinstance(slot_id, str) else slot_id,
                    "sig": sig,
                    "chain_length": chain_len,
                    "has_err": tx.get("has_err", False),
                })
                n_tx += 1

        print(f"[{i+1}/{len(json_files)}] {filename} | 交易数: {n_tx}", flush=True)

    # 全量 JSON 可能很大，仅保存前 50000 条样本到 JSON；CSV 可全量
    sample = rows[:50000] if len(rows) > 50000 else rows
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    print(f"\n已保存 (样本至多 50000 条): {OUTPUT_JSON}")

    with open(OUTPUT_CSV, "w", encoding="utf-8") as f:
        f.write("slot_range,slot,sig,chain_length,has_err\n")
        for r in rows:
            f.write(
                f"{r['slot_range']},{r['slot']},{r['sig']},{r['chain_length']},{r['has_err']}\n")
    print(f"已保存 (全量): {OUTPUT_CSV}")

    if rows:
        chains = [r["chain_length"] for r in rows]
        print(f"\n总交易数: {len(rows)}")
        print(
            f"chain_length: min={min(chains):.2f}, max={max(chains):.2f}, mean={sum(chains)/len(chains):.2f}")


if __name__ == "__main__":
    main()
