# # import json
# # import os


# # def detect_sandwich_attacks(file_path):
# #     if not os.path.exists(file_path):
# #         print(f"Error: file not found {file_path}")
# #         return

# #     with open(file_path, 'r', encoding='utf-8') as f:
# #         try:
# #             data = json.load(f)
# #         except Exception as e:
# #             print(f"Parse error: {e}")
# #             return

# #     all_found_attacks = []

# #     for slot_id, transactions in data.items():
# #         # 1. Preprocessing: extract Pool accounts and Signer for each Mint in every transaction
# #         tx_info = []
# #         slot_attacks_before = len(all_found_attacks)

# #         for idx, tx in enumerate(transactions):
# #             if tx.get('has_err'):
# #                 continue

# #             # Get the current transaction's Signer (first address in accounts array)
# #             current_signer = tx['accounts'][0]
# #             pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount']
# #                        or 0 for b in tx.get('pre_token_balances', [])}

# #             mint_data = {}
# #             all_changes = {}

# #             for post in tx.get('post_token_balances', []):
# #                 acc_idx = post['accountIndex']
# #                 post_amt = post['uiTokenAmount']['uiAmount'] or 0
# #                 pre_amt = pre_map.get(acc_idx, 0)
# #                 diff = post_amt - pre_amt
# #                 if abs(diff) > 1e-9:
# #                     all_changes[acc_idx] = {
# #                         'diff': diff, 'mint': post['mint'], 'owner': post['owner']}

# #             # Find the counterparty (pool) for Signer's balance change
# #             for acc_idx, info in all_changes.items():
# #                 if info['owner'] == current_signer:
# #                     target_mint = info['mint']
# #                     signer_diff = info['diff']

# #                     for other_idx, other_info in all_changes.items():
# #                         if other_info['mint'] == target_mint and other_info['owner'] != current_signer:
# #                             # Verify pool logic: opposite direction changes with similar amounts
# #                             if abs(other_info['diff'] + signer_diff) < abs(signer_diff) * 0.001 + 1e-10:
# #                                 mint_data[target_mint] = {
# #                                     'signer_change': signer_diff,
# #                                     'pool_account': other_info['owner']
# #                                 }

# #             tx_info.append({
# #                 'idx': idx,
# #                 'sig': tx['sig'],
# #                 'signer': current_signer,
# #                 'mint_data': mint_data
# #             })

# #         # 2. Match sandwiches: same attacker Signer, same Mint, same pool
# #         for i, front in enumerate(tx_info):
# #             for target_mint, f_data in front['mint_data'].items():
# #                 if f_data['signer_change'] <= 0:
# #                     continue
# #                 if target_mint == "So11111111111111111111111111111111111111112":
# #                     continue

# #                 for j in range(i + 1, len(tx_info)):
# #                     back = tx_info[j]
# #                     if back['signer'] == front['signer'] and target_mint in back['mint_data']:
# #                         b_data = back['mint_data'][target_mint]

# #                         # Check if backrun sells in the same pool
# #                         if b_data['signer_change'] < 0 and b_data['pool_account'] == f_data['pool_account']:

# #                             # 3. Find intermediate victims
# #                             victims = []
# #                             for k in range(i + 1, j):
# #                                 mid = tx_info[k]
# #                                 if target_mint in mid['mint_data']:
# #                                     m_data = mid['mint_data'][target_mint]
# #                                     # Criterion: victim buys in the same pool and Signer is not the attacker
# #                                     if m_data['signer_change'] > 0 and m_data['pool_account'] == f_data['pool_account']:
# #                                         if mid['signer'] != front['signer']:
# #                                             victims.append({
# #                                                 'sig': mid['sig'],
# #                                                 # Added: victim's Signer
# #                                                 'signer': mid['signer'],
# #                                                 'amount': m_data['signer_change']
# #                                             })

# #                             if victims:
# #                                 all_found_attacks.append({
# #                                     "slot": slot_id,
# #                                     "attacker": front['signer'],
# #                                     "pool": f_data['pool_account'],
# #                                     "mint": target_mint,
# #                                     "front_sig": front['sig'],
# #                                     "back_sig": back['sig'],
# #                                     "victims": victims
# #                                 })
# #                             break

# #         slot_attacks_found = len(all_found_attacks) - slot_attacks_before
# #         print(
# #             f"Slot {slot_id}: {len(transactions)} transactions, {len(tx_info)} valid, detected {slot_attacks_found} attacks")

# #     # 3. Output results
# #     if not all_found_attacks:
# #         print("\n[Result] No qualifying sandwich attacks detected.")
# #     else:
# #         print(f"\nSuccessfully captured {len(all_found_attacks)} sandwich attack sequences:")
# #         for atk in all_found_attacks:
# #             print(f"\n" + "="*95)
# #             print(f"SLOT: {atk['slot']} | Target token: {atk['mint']}")
# #             print(f"Pool Vault: {atk['pool']}")
# #             print(f"Attacker (MEV Bot): {atk['attacker']}")
# #             print(f"Frontrun buy (Front): {atk['front_sig']}")
# #             print(f"Backrun sell (Back) : {atk['back_sig']}")
# #             print(f"Sandwiched transaction details ({len(atk['victims'])} txs):")
# #             for idx, v in enumerate(atk['victims'], 1):
# #                 print(f"   [{idx}] Victim address: {v['signer']}")
# #                 print(f"       Buy amount  : {v['amount']:.6f}")
# #                 print(f"       Tx signature: {v['sig']}")
# #         print("="*95 + "\n")


# # if __name__ == "__main__":
# #     file_name = "mev_full_analysis_324115988_324115989.json"
# #     detect_sandwich_attacks(file_name)


# import json
# import os


# def detect_sandwich_attacks_robust(file_path):
#     if not os.path.exists(file_path):
#         print(f"Error: file not found {file_path}")
#         return

#     with open(file_path, 'r', encoding='utf-8') as f:
#         try:
#             data = json.load(f)
#         except Exception as e:
#             print(f"Parse error: {e}")
#             return

#     all_found_attacks = []
#     # Sort by slot number
#     sorted_slots = sorted(data.keys(), key=lambda x: int(x))

#     for slot_id in sorted_slots:
#         transactions = data[slot_id]
#         tx_info = []

#         for tx in transactions:
#             if tx.get('has_err'):
#                 continue

#             signer = tx['accounts'][0]
#             sig = tx['sig']

#             # 1. Build balance change mapping
#             pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
#                        for b in tx.get('pre_token_balances', [])}

#             # Aggregate all changes for each Mint in this transaction
#             # { mint: { 'signer_change': 0, 'pool_changes': { owner: change } } }
#             mint_summary = {}

#             for post in tx.get('post_token_balances', []):
#                 mint = post['mint']
#                 owner = post['owner']
#                 post_amt = post['uiTokenAmount']['uiAmount'] or 0
#                 pre_amt = pre_map.get(post['accountIndex'], 0)
#                 diff = post_amt - pre_amt

#                 if abs(diff) < 1e-9:
#                     continue

#                 if mint not in mint_summary:
#                     mint_summary[mint] = {'signer_change': 0, 'pools': {}}

#                 if owner == signer:
#                     mint_summary[mint]['signer_change'] += diff
#                 else:
#                     # Record the pool's change
#                     mint_summary[mint]['pools'][owner] = mint_summary[mint]['pools'].get(
#                         owner, 0) + diff

#             # 2. Determine whether this transaction contains a valid Swap
#             # Definition: Signer has a change and a pool exists with opposite direction, amount within 3% tolerance
#             valid_mint_data = {}
#             for mint, info in mint_summary.items():
#                 s_diff = info['signer_change']
#                 if abs(s_diff) < 1e-9:
#                     continue

#                 for p_owner, p_diff in info['pools'].items():
#                     # Criterion: opposite directions (one in, one out) and amounts close (tolerating 3% fee/slippage)
#                     if s_diff * p_diff < 0:
#                         if abs(abs(p_diff) - abs(s_diff)) < abs(s_diff) * 0.03 + 1e-6:
#                             valid_mint_data[mint] = {
#                                 'signer_change': s_diff,
#                                 'pool_account': p_owner
#                             }
#                             break  # Found the main pool, stop

#             if valid_mint_data:
#                 tx_info.append({
#                     'sig': sig,
#                     'signer': signer,
#                     'mint_data': valid_mint_data
#                 })

#         # 3. Match sandwiches (intra-slot logic)
#         slot_attacks = []
#         for i, front in enumerate(tx_info):
#             for mint, f_data in front['mint_data'].items():
#                 if f_data['signer_change'] <= 0:
#                     continue  # Front must be a buy
#                 if mint == "So11111111111111111111111111111111111111112":
#                     continue

#                 for j in range(i + 1, len(tx_info)):
#                     back = tx_info[j]
#                     # Same attacker, same Mint
#                     if back['signer'] == front['signer'] and mint in back['mint_data']:
#                         b_data = back['mint_data'][mint]
#                         # Sell in the same pool
#                         if b_data['signer_change'] < 0 and b_data['pool_account'] == f_data['pool_account']:

#                             victims = []
#                             for k in range(i + 1, j):
#                                 mid = tx_info[k]
#                                 if mint in mid['mint_data']:
#                                     m_data = mid['mint_data'][mint]
#                                     if m_data['signer_change'] > 0 and m_data['pool_account'] == f_data['pool_account']:
#                                         if mid['signer'] != front['signer']:
#                                             victims.append({
#                                                 'sig': mid['sig'],
#                                                 'signer': mid['signer'],
#                                                 'amount': m_data['signer_change']
#                                             })

#                             if victims:
#                                 slot_attacks.append({
#                                     "slot": slot_id,
#                                     "attacker": front['signer'],
#                                     "pool": f_data['pool_account'],
#                                     "mint": mint,
#                                     "front_sig": front['sig'],
#                                     "back_sig": back['sig'],
#                                     "victims": victims
#                                 })
#                             break

#         print(
#             f"Slot {slot_id}: total txs {len(transactions)} | valid swaps {len(tx_info)} | sandwiches found {len(slot_attacks)}")
#         all_found_attacks.extend(slot_attacks)

#     # 4. Output
#     _final_report(all_found_attacks)


# def _final_report(attacks):
#     if not attacks:
#         print("\n[Result] No sandwich attacks detected.")
#         return
#     print(f"\nSuccessfully captured {len(attacks)} sandwich attacks:")
#     for atk in attacks:
#         print(f"\n" + "—"*80)
#         print(f"Slot: {atk['slot']} | Mint: {atk['mint']}")
#         print(f"Attacker: {atk['attacker']}")
#         print(f"Pool: {atk['pool']}")
#         print(f"Front: {atk['front_sig']}")
#         print(f"Back : {atk['back_sig']}")
#         print(f"Victim count: {len(atk['victims'])}")
#         for v in atk['victims']:
#             print(f"   - {v['signer']} | bought: {v['amount']:.4f}")


# if __name__ == "__main__":
#     detect_sandwich_attacks_robust(
#         "mev_324115988.json")


import json
import os


def detect_sandwich_attacks_simple(file_path):
    if not os.path.exists(file_path):
        print(f"Error: file not found {file_path}")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    all_found_attacks = []
    sorted_slots = sorted(data.keys(), key=lambda x: int(x))

    for slot_id in sorted_slots:
        transactions = data[slot_id]
        tx_info = []

        for tx in transactions:
            if tx.get('has_err'):
                continue

            signer = tx['accounts'][0]
            sig = tx['sig']

            pre_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
                       for b in tx.get('pre_token_balances', [])}

            # Directly record signer change for each (mint, pool)
            swaps = {}  # key: (mint, pool), value: signer_change

            for post in tx.get('post_token_balances', []):
                mint = post['mint']
                owner = post['owner']
                post_amt = post['uiTokenAmount']['uiAmount'] or 0
                pre_amt = pre_map.get(post['accountIndex'], 0)
                diff = post_amt - pre_amt

                if abs(diff) < 1e-9:
                    continue

                if owner == signer:
                    # Signer's balance change; temporarily use None as pool placeholder
                    for post2 in tx.get('post_token_balances', []):
                        if post2['mint'] == mint and post2['owner'] != signer:
                            pool = post2['owner']
                            pool_diff = (
                                post2['uiTokenAmount']['uiAmount'] or 0) - pre_map.get(post2['accountIndex'], 0)
                            # Record as long as the direction is opposite
                            if diff * pool_diff < 0:
                                swaps[(mint, pool)] = diff
                                break

            if swaps:
                tx_info.append({
                    'sig': sig,
                    'signer': signer,
                    'swaps': swaps  # {(mint, pool): signer_change}
                })

        # Match sandwiches
        slot_attacks = []
        for i, front in enumerate(tx_info):
            for (mint, pool), f_change in front['swaps'].items():
                if f_change <= 0:
                    continue  # Front must be a buy
                if mint == "So11111111111111111111111111111111111111112":
                    continue

                for j in range(i + 1, len(tx_info)):
                    back = tx_info[j]
                    if back['signer'] != front['signer']:
                        continue

                    b_change = back['swaps'].get((mint, pool))
                    if b_change is not None and b_change < 0:  # Sell in the same pool
                        victims = []
                        for k in range(i + 1, j):
                            mid = tx_info[k]
                            if mid['signer'] == front['signer']:
                                continue
                            m_change = mid['swaps'].get((mint, pool))
                            if m_change is not None and m_change > 0:
                                victims.append({
                                    'sig': mid['sig'],
                                    'signer': mid['signer'],
                                    'amount': m_change
                                })

                        if victims:
                            slot_attacks.append({
                                "slot": slot_id,
                                "attacker": front['signer'],
                                "pool": pool,
                                "mint": mint,
                                "front_sig": front['sig'],
                                "back_sig": back['sig'],
                                "front_amount": f_change,
                                "back_amount": abs(b_change),
                                "profit": abs(b_change) - f_change,
                                "victims": victims
                            })
                        break

        all_found_attacks.extend(slot_attacks)

    _final_report(all_found_attacks)


def _final_report(attacks):
    if not attacks:
        print("\n[Result] No sandwich attacks detected.")
        return

    print(f"\nSuccessfully captured {len(attacks)} sandwich attacks:")
    for atk in attacks:
        print(f"\n" + "—"*80)
        print(f"Slot: {atk['slot']} | Mint: {atk['mint'][:8]}...")
        print(f"Attacker: {atk['attacker']}")
        print(
            f"Profit: {atk['profit']:.6f} (bought {atk['front_amount']:.4f} -> sold {atk['back_amount']:.4f})")
        print(f"Victims: {len(atk['victims'])}")


if __name__ == "__main__":
    detect_sandwich_attacks_simple("mev_324115989.json")
