"""
Batch detect sandwich attacks in all JSON files under datasource/data, tallying successful and failed transactions.
"""
import json
import os
import re
import sys
from collections import defaultdict

# Force flush output
sys.stdout.reconfigure(line_buffering=True)

# Data directory
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "datasource", "data")
OUTPUT_PATH = os.path.join(os.path.dirname(
    __file__), "batch_sandwich_summary.json")


def extract_slot_range(filename):
    """Extract slot range from filename"""
    match = re.search(r'mev_(\d+)_(\d+)\.json', filename)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return filename


def detect_sandwich_in_data(data, include_failed=False):
    """
    Detect sandwich attacks.
    include_failed: whether to include failed transactions
    Returns: (sandwich count in successful txs, sandwich count in failed txs)
    """
    success_attacks = []
    failed_attacks = []

    sorted_slots = sorted(data.keys(), key=lambda x: int(x))

    for slot_id in sorted_slots:
        transactions = data[slot_id]

        # Process successful and failed transactions separately
        for is_failed in [False, True]:
            tx_info = []
            slot_signer_count = defaultdict(int)

            for tx in transactions:
                has_err = tx.get('has_err', False)
                if has_err != is_failed:
                    continue

                signer = tx['accounts'][0]
                sig = tx['sig']

                pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
                           for b in tx.get('pre_token_balances', [])}

                mint_changes = defaultdict(dict)

                for post in tx.get('post_token_balances', []):
                    mint = post['mint']
                    owner = post.get('owner')
                    if not owner:
                        continue
                    post_amt = post['uiTokenAmount']['uiAmount'] or 0
                    pre_amt = pre_map.get(post['accountIndex'], 0)
                    diff = post_amt - pre_amt

                    if abs(diff) > 1e-9:
                        mint_changes[mint][owner] = mint_changes[mint].get(
                            owner, 0) + diff

                mint_info = {}
                for mint, owners in mint_changes.items():
                    if signer not in owners:
                        continue

                    signer_change = owners[signer]
                    pools = [o for o in owners.keys() if o != signer]

                    mint_info[mint] = {
                        'signer_change': signer_change,
                        'pools': pools
                    }

                if mint_info:
                    slot_signer_count[signer] += 1
                    tx_info.append({
                        'sig': sig,
                        'signer': signer,
                        'mint_info': mint_info
                    })

            # Detect sandwiches
            slot_attacks = []
            for i, front in enumerate(tx_info):
                for mint, f_info in front['mint_info'].items():
                    if mint == "So11111111111111111111111111111111111111112":
                        continue
                    if f_info['signer_change'] <= 0:
                        continue

                    f_pools = set(f_info['pools'])

                    for j in range(i + 1, len(tx_info)):
                        back = tx_info[j]
                        if back['signer'] != front['signer']:
                            continue
                        if mint not in back['mint_info']:
                            continue

                        b_info = back['mint_info'][mint]
                        if b_info['signer_change'] >= 0:
                            continue

                        common_pools = f_pools & set(b_info['pools'])
                        if not common_pools:
                            continue

                        # Check for victims
                        victims = []
                        for k in range(i + 1, j):
                            mid = tx_info[k]
                            if mid['signer'] == front['signer']:
                                continue
                            if mint not in mid['mint_info']:
                                continue

                            m_info = mid['mint_info'][mint]
                            if m_info['signer_change'] > 0 and (set(m_info['pools']) & common_pools):
                                victims.append(mid['sig'])

                        if victims:
                            slot_attacks.append({
                                "slot": slot_id,
                                "attacker": front['signer'],
                                "mint": mint,
                                "victims_count": len(victims)
                            })
                        break

            if is_failed:
                failed_attacks.extend(slot_attacks)
            else:
                success_attacks.extend(slot_attacks)

    return len(success_attacks), len(failed_attacks)


def main():
    if not os.path.exists(DATA_DIR):
        print(f"Error: data directory not found {DATA_DIR}")
        return

    # Get all JSON files
    json_files = [f for f in os.listdir(DATA_DIR)
                  if f.endswith('.json') and not f.startswith('._')]
    json_files.sort()

    print(f"Found {len(json_files)} JSON files\n", flush=True)

    summary = {}

    for i, filename in enumerate(json_files):
        file_path = os.path.join(DATA_DIR, filename)
        slot_range = extract_slot_range(filename)

        print(f"[{i+1}/{len(json_files)}] Processing: {filename}", flush=True)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"    Parse error: {e}", flush=True)
            continue

        success_count, failed_count = detect_sandwich_in_data(data)
        total_count = success_count + failed_count

        summary[slot_range] = {
            "sandwich_success": success_count,
            "sandwich_failed": failed_count,
            "sandwich_total": total_count
        }

        print(
            f"    Sandwich success: {success_count}, failed: {failed_count}, total: {total_count}", flush=True)

    # Save summary results
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"Summary results saved to: {OUTPUT_PATH}")
    print(f"{'='*60}")

    # Print overall statistics
    total_success = sum(v['sandwich_success'] for v in summary.values())
    total_failed = sum(v['sandwich_failed'] for v in summary.values())
    total_all = total_success + total_failed

    print(f"\nOverall statistics:")
    print(f"  Sandwich attacks (successful txs): {total_success}")
    print(f"  Sandwich attacks (failed txs):     {total_failed}")
    print(f"  Total sandwich attacks:            {total_all}")


if __name__ == "__main__":
    main()
