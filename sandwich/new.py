# import json
# import os
# from collections import defaultdict


# def extract_tx_info(file_path, output_path="tx_info_debug.json"):
#     """Step 1: Extract transaction info and save locally"""
#     if not os.path.exists(file_path):
#         print(f"Error: file not found {file_path}")
#         return

#     with open(file_path, 'r', encoding='utf-8') as f:
#         data = json.load(f)

#     result = {}
#     signer_count = defaultdict(int)  # Count occurrences of each signer
#     sorted_slots = sorted(data.keys(), key=lambda x: int(x))

#     for slot_id in sorted_slots:
#         transactions = data[slot_id]
#         tx_info = []

#         for tx in transactions:
#             if tx.get('has_err'):
#                 continue

#             signer = tx['accounts'][0]
#             sig = tx['sig']

#             pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
#                        for b in tx.get('pre_token_balances', [])}

#             # Collect all owners and their changes for each mint
#             mint_changes = defaultdict(dict)  # {mint: {owner: change}}

#             for post in tx.get('post_token_balances', []):
#                 mint = post['mint']
#                 owner = post['owner']
#                 post_amt = post['uiTokenAmount']['uiAmount'] or 0
#                 pre_amt = pre_map.get(post['accountIndex'], 0)
#                 diff = post_amt - pre_amt

#                 if abs(diff) > 1e-9:
#                     mint_changes[mint][owner] = mint_changes[mint].get(
#                         owner, 0) + diff

#             # Organize into mint_info format
#             mint_info = {}
#             for mint, owners in mint_changes.items():
#                 if signer not in owners:
#                     continue  # Skip mints where signer has no change

#                 signer_change = owners[signer]
#                 # pool only keeps non-signer owners with changes under the same mint
#                 pools = [o for o in owners.keys() if o != signer]

#                 mint_info[mint] = {
#                     'signer_change': signer_change,
#                     'pools': pools
#                 }

#             if mint_info:
#                 signer_count[signer] += 1
#                 tx_info.append({
#                     'sig': sig,
#                     'signer': signer,
#                     'mint_info': mint_info
#                 })

#         result[slot_id] = tx_info

#     # Save transaction info
#     with open(output_path, 'w', encoding='utf-8') as f:
#         json.dump(result, f, indent=2, ensure_ascii=False)

#     # Save signer statistics
#     signer_stats_path = output_path.replace('.json', '_signer_stats.json')
#     sorted_signers = sorted(signer_count.items(), key=lambda x: -x[1])
#     with open(signer_stats_path, 'w', encoding='utf-8') as f:
#         json.dump(dict(sorted_signers), f, indent=2, ensure_ascii=False)

#     print(f"Transaction info saved to {output_path}")
#     print(f"Signer statistics saved to {signer_stats_path}")
#     print(f"   Total {len(result)} slots")
#     total_tx = sum(len(v) for v in result.values())
#     print(f"   Total {total_tx} valid transactions")
#     print(f"   Total {len(signer_count)} distinct signers")
#     print(f"\nTop 10 most frequent signers:")
#     for signer, count in sorted_signers[:10]:
#         print(f"   {signer[:16]}... : {count} times")

#     return result


# def detect_sandwich_attacks(tx_info_path="tx_info_debug.json"):
#     """Step 2: Read from local file and detect sandwiches"""
#     with open(tx_info_path, 'r', encoding='utf-8') as f:
#         data = json.load(f)

#     all_found_attacks = []
#     sorted_slots = sorted(data.keys(), key=lambda x: int(x))

#     for slot_id in sorted_slots:
#         tx_info = data[slot_id]

#         slot_attacks = []
#         for i, front in enumerate(tx_info):
#             for mint, f_info in front['mint_info'].items():
#                 if mint == "So11111111111111111111111111111111111111112":
#                     continue
#                 if f_info['signer_change'] <= 0:
#                     continue

#                 f_pools = set(f_info['pools'])

#                 for j in range(i + 1, len(tx_info)):
#                     back = tx_info[j]
#                     if back['signer'] != front['signer']:
#                         continue
#                     if mint not in back['mint_info']:
#                         continue

#                     b_info = back['mint_info'][mint]
#                     if b_info['signer_change'] >= 0:
#                         continue

#                     common_pools = f_pools & set(b_info['pools'])
#                     if not common_pools:
#                         continue

#                     victims = []
#                     for k in range(i + 1, j):
#                         mid = tx_info[k]
#                         if mid['signer'] == front['signer']:
#                             continue
#                         if mint not in mid['mint_info']:
#                             continue

#                         m_info = mid['mint_info'][mint]
#                         if m_info['signer_change'] > 0 and (set(m_info['pools']) & common_pools):
#                             victims.append({
#                                 'sig': mid['sig'],
#                                 'signer': mid['signer'],
#                                 'amount': m_info['signer_change']
#                             })

#                     if victims:
#                         slot_attacks.append({
#                             "slot": slot_id,
#                             "attacker": front['signer'],
#                             "mint": mint,
#                             "pool": list(common_pools)[0],
#                             "front_sig": front['sig'],
#                             "back_sig": back['sig'],
#                             "front_amount": f_info['signer_change'],
#                             "back_amount": abs(b_info['signer_change']),
#                             "profit": abs(b_info['signer_change']) - f_info['signer_change'],
#                             "victims": victims
#                         })
#                     break

#         all_found_attacks.extend(slot_attacks)

#     _final_report(all_found_attacks)
#     return all_found_attacks


# def _final_report(attacks):
#     if not attacks:
#         print("\n[Result] No sandwich attacks detected.")
#         return

#     print(f"\nSuccessfully captured {len(attacks)} sandwich attacks:")
#     for atk in attacks:
#         print(f"\n" + "—"*80)
#         print(f"Slot: {atk['slot']}")
#         print(f"Mint: {atk['mint']}")
#         print(f"Pool: {atk['pool']}")
#         print(f"Attacker: {atk['attacker']}")
#         print(f"Front: {atk['front_sig']} (bought {atk['front_amount']:.6f})")
#         print(f"Back: {atk['back_sig']} (sold {atk['back_amount']:.6f})")
#         print(f"Profit: {atk['profit']:.6f}")
#         print(f"Victims: {len(atk['victims'])}")


# if __name__ == "__main__":
#     extract_tx_info("mev_full_analysis_324115990_324115990.json",
#                     "tx_info_debug.json")
#     detect_sandwich_attacks("tx_info_debug.json")


import json
import os
from collections import defaultdict


def extract_tx_info(file_path, output_path="tx_info_debug.json"):
    """Step 1: Extract transaction info and save locally"""
    if not os.path.exists(file_path):
        print(f"Error: file not found {file_path}")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    result = {}
    sorted_slots = sorted(data.keys(), key=lambda x: int(x))

    for slot_id in sorted_slots:
        transactions = data[slot_id]
        tx_info = []
        slot_signer_count = defaultdict(int)  # Signer occurrence count within this slot

        for tx in transactions:
            if tx.get('has_err'):
                continue

            signer = tx['accounts'][0]
            sig = tx['sig']

            pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
                       for b in tx.get('pre_token_balances', [])}

            mint_changes = defaultdict(dict)

            for post in tx.get('post_token_balances', []):
                mint = post['mint']
                owner = post['owner']
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

        # Backfill signer_count_in_slot
        for tx in tx_info:
            tx['signer_count_in_slot'] = slot_signer_count[tx['signer']]

        result[slot_id] = {
            'transactions': tx_info,
            # Only keep entries with count >= 2
            'signer_counts': {k: v for k, v in slot_signer_count.items() if v >= 2}
        }

    # Save
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Statistics
    total_tx = sum(len(v['transactions']) for v in result.values())
    multi_signer_slots = sum(1 for v in result.values() if v['signer_counts'])

    print(f"Saved to {output_path}")
    print(f"   Total {len(result)} slots")
    print(f"   Total {total_tx} valid transactions")
    print(f"   {multi_signer_slots} slots have the same signer appearing >= 2 times")

    return result


def detect_sandwich_attacks(tx_info_path="tx_info_debug.json"):
    """Step 2: Read from local file and detect sandwiches"""
    with open(tx_info_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    all_found_attacks = []
    sorted_slots = sorted(data.keys(), key=lambda x: int(x))

    for slot_id in sorted_slots:
        tx_info = data[slot_id]['transactions']

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

                    victims = []
                    for k in range(i + 1, j):
                        mid = tx_info[k]
                        if mid['signer'] == front['signer']:
                            continue
                        if mint not in mid['mint_info']:
                            continue

                        m_info = mid['mint_info'][mint]
                        if m_info['signer_change'] > 0 and (set(m_info['pools']) & common_pools):
                            victims.append({
                                'sig': mid['sig'],
                                'signer': mid['signer'],
                                'amount': m_info['signer_change']
                            })

                    if victims:
                        slot_attacks.append({
                            "slot": slot_id,
                            "attacker": front['signer'],
                            "mint": mint,
                            "pool": list(common_pools)[0],
                            "front_sig": front['sig'],
                            "back_sig": back['sig'],
                            "front_amount": f_info['signer_change'],
                            "back_amount": abs(b_info['signer_change']),
                            "profit": abs(b_info['signer_change']) - f_info['signer_change'],
                            "victims": victims
                        })
                    break

        all_found_attacks.extend(slot_attacks)

    _final_report(all_found_attacks)
    return all_found_attacks


def _final_report(attacks):
    if not attacks:
        print("\n[Result] No sandwich attacks detected.")
        return

    print(f"\nSuccessfully captured {len(attacks)} sandwich attacks:")
    for atk in attacks:
        print(f"\n" + "—"*80)
        print(f"Slot: {atk['slot']}")
        print(f"Mint: {atk['mint']}")
        print(f"Pool: {atk['pool']}")
        print(f"Attacker: {atk['attacker']}")
        print(f"Front: {atk['front_sig']} (bought {atk['front_amount']:.6f})")
        print(f"Back: {atk['back_sig']} (sold {atk['back_amount']:.6f})")
        print(f"Profit: {atk['profit']:.6f}")
        print(f"Victims: {len(atk['victims'])}")


if __name__ == "__main__":
    extract_tx_info("mev_full_analysis_252308623_252308633.json",
                    "tx_info_debug.json")
    detect_sandwich_attacks("tx_info_debug.json")
