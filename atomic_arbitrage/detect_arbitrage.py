"""
Unified arbitrage detection program: detects SOL, USDC, and USDT arbitrage transactions.
"""
import json
import os

# Token Mint addresses
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


def get_chain_length(tx):
    """
    Transaction chain length: count of transfer-related occurrences in logs / 2, floored
    (used as a proxy indicator for intermediate transactions/steps).
    """
    logs = tx.get("logs") or tx.get("logMessages") or []
    transfer_count = sum(
        1 for line in logs if "transfer" in (line or "").lower())
    return transfer_count // 2


def get_signer_sol_cost(tx, num_signers):
    """Calculate the total SOL cost paid by all signers (negative balance change portions)"""
    total_cost = 0

    for i in range(num_signers):
        if i < len(tx['pre_balances']) and i < len(tx['post_balances']):
            sol_change = (tx['post_balances'][i] - tx['pre_balances'][i]) / 1e9
            # Only count the SOL decrease portion (cost paid)
            if sol_change < 0:
                total_cost += abs(sol_change)

    return total_cost


def get_signers(tx, num_signers):
    """Get a list of signer addresses for the specified number of signers"""
    return tx['accounts'][:num_signers]


def detect_sol_arbitrage(tx, slot_id):
    """Detect SOL arbitrage (supports 1 or 2 signers, SOL cost is the sum of all signers' balance changes)"""
    if tx.get('has_err'):
        return None

    num_signers = tx.get("num_signers", 1)
    signers = get_signers(tx, num_signers)
    signer_set = set(signers)

    # SOL change: sum of all signers' SOL balance changes (in SOL)
    sol_change_total = 0
    for i in range(num_signers):
        if i < len(tx.get('pre_balances', [])) and i < len(tx.get('post_balances', [])):
            sol_change_total += (tx['post_balances']
                                 [i] - tx['pre_balances'][i]) / 1e9
    if sol_change_total >= 0:
        return None

    pre_token_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
                     for b in tx.get('pre_token_balances', [])}

    signer_wsol_gain = 0
    has_non_signer_drain = False
    is_impure = False

    for post in tx.get('post_token_balances', []):
        acc_idx = post['accountIndex']
        mint = post['mint']
        owner = post.get('owner')
        post_amt = post['uiTokenAmount']['uiAmount'] or 0
        pre_amt = pre_token_map.get(acc_idx, 0) or 0
        change = post_amt - pre_amt

        if abs(change) < 1e-9:
            continue

        if owner in signer_set:
            if mint == WSOL_MINT:
                signer_wsol_gain += change
            else:
                is_impure = True
        else:
            if change < -1e-9:
                has_non_signer_drain = True

    if not is_impure and has_non_signer_drain:
        net_profit = signer_wsol_gain + sol_change_total
        if net_profit > 0.00001:
            sol_cost = get_signer_sol_cost(tx, num_signers)
            return {
                "type": "SOL",
                "slot": slot_id,
                "sig": tx['sig'],
                "signers": signers,
                "num_signers": num_signers,
                "sol_cost": sol_cost,
                "sol_spent": abs(sol_change_total),
                "wsol_gain": signer_wsol_gain,
                "profit": net_profit,
                "profit_unit": "SOL",
                "chain_length": get_chain_length(tx),
            }
    return None


def detect_stablecoin_arbitrage(tx, slot_id, mint, token_name):
    """Detect stablecoin arbitrage (USDC/USDT) - defaults to 2 signers"""
    if tx.get('has_err'):
        return None

    num_signers = 2  # USDC/USDT arbitrage defaults to 2 signers
    signers = get_signers(tx, num_signers)
    signer_set = set(signers)
    signer_profit = 0
    pool_increases = []

    pre_balances = {b['accountIndex']: float(b['uiTokenAmount']['uiAmount'] or 0)
                    for b in tx.get('pre_token_balances', [])}

    for balance in tx.get('post_token_balances', []):
        if balance['mint'] != mint:
            continue

        acc_idx = balance['accountIndex']
        owner = balance.get('owner')
        post_amt = float(balance['uiTokenAmount']['uiAmount'] or 0)
        pre_amt = pre_balances.get(acc_idx, 0.0)
        diff = post_amt - pre_amt

        if owner in signer_set:
            signer_profit += diff
        elif diff > 0:
            pool_increases.append(diff)

    if not pool_increases or signer_profit <= 1e-6:
        return None

    max_pool_inc = max(pool_increases)

    if max_pool_inc > signer_profit * 1:
        leverage = signer_profit / max_pool_inc
        sol_cost = get_signer_sol_cost(tx, num_signers)
        return {
            "type": token_name,
            "slot": slot_id,
            "sig": tx['sig'],
            "signers": signers,
            "num_signers": num_signers,
            "sol_cost": sol_cost,
            "profit": signer_profit,
            "pool_flow": max_pool_inc,
            "leverage": leverage,
            "profit_unit": token_name,
            "chain_length": get_chain_length(tx),
        }
    return None


def analyze_file(file_path):
    """Analyze a single file and return all detected arbitrages"""
    if not os.path.exists(file_path):
        print(f"Error: file not found {file_path}")
        return []

    print(f"Analyzing: {file_path} ...")
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"Parse error: {e}")
            return []

    all_arbs = []

    for slot_id, transactions in data.items():
        for tx in transactions:
            # Detect SOL arbitrage
            sol_arb = detect_sol_arbitrage(tx, slot_id)
            if sol_arb:
                all_arbs.append(sol_arb)

            # Detect USDC arbitrage
            usdc_arb = detect_stablecoin_arbitrage(
                tx, slot_id, USDC_MINT, "USDC")
            if usdc_arb:
                all_arbs.append(usdc_arb)

            # Detect USDT arbitrage
            usdt_arb = detect_stablecoin_arbitrage(
                tx, slot_id, USDT_MINT, "USDT")
            if usdt_arb:
                all_arbs.append(usdt_arb)

    return all_arbs


def print_results(arbs):
    """Print detection results"""
    if not arbs:
        print("\n[Result] No arbitrage transactions detected.")
        return

    # Group by type
    sol_arbs = [a for a in arbs if a['type'] == 'SOL']
    usdc_arbs = [a for a in arbs if a['type'] == 'USDC']
    usdt_arbs = [a for a in arbs if a['type'] == 'USDT']

    print(f"\n{'='*60}")
    print(f"Arbitrage Detection Results Summary")
    print(f"{'='*60}")
    print(f"SOL arbitrage:  {len(sol_arbs)} tx")
    print(f"USDC arbitrage: {len(usdc_arbs)} tx")
    print(f"USDT arbitrage: {len(usdt_arbs)} tx")
    print(f"Total:          {len(arbs)} tx")
    print(f"{'='*60}\n")

    # Print SOL arbitrage
    if sol_arbs:
        print("SOL Arbitrage Details:")
        for arb in sol_arbs:
            print(
                f"  Slot: {arb['slot']} | Sig: {arb['sig'][:20]}... | Profit: {arb['profit']:.6f} SOL")

    # Print USDC arbitrage
    if usdc_arbs:
        print("\nUSDC Arbitrage Details:")
        for arb in usdc_arbs:
            print(
                f"  Slot: {arb['slot']} | Sig: {arb['sig'][:20]}... | Profit: {arb['profit']:.4f} USDC")

    # Print USDT arbitrage
    if usdt_arbs:
        print("\nUSDT Arbitrage Details:")
        for arb in usdt_arbs:
            print(
                f"  Slot: {arb['slot']} | Sig: {arb['sig'][:20]}... | Profit: {arb['profit']:.4f} USDT")


def save_results(arbs, output_path):
    """Save detection results to a local JSON file"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(arbs, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {output_path}")


def main(file_path, save_to_file=True):
    """Main function"""
    arbs = analyze_file(file_path)
    print_results(arbs)

    # Save results locally
    if save_to_file and arbs:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_path = os.path.join(os.path.dirname(
            file_path) or ".", f"arbitrage_results_{base_name}.json")
        save_results(arbs, output_path)

    return arbs


if __name__ == "__main__":
    # Default file to analyze
    main("mev_281715548_281715698.json")
