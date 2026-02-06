import json
import os


def detect_ultra_pure_sol_arbitrage(file_path):
    if not os.path.exists(file_path):
        print(f"错误: 找不到文件 {file_path}")
        return

    print(f"正在读取文件: {file_path} ...")
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"解析错误: {e}")
            return

    wsol_mint = "So11111111111111111111111111111111111111112"
    found_arbs = []

    for slot_id, transactions in data.items():
        for tx in transactions:
            if tx.get('has_err'):
                continue

            signer = tx['accounts'][0]

            # 1. SOL 变动判定 (原生 SOL 必须减少)
            sol_change = (tx['post_balances'][0] - tx['pre_balances'][0]) / 1e9
            if sol_change >= 0:
                continue

            # 2. 分析 Token 账户变动
            pre_token_map = {b['accountIndex']: b['uiTokenAmount']['uiAmount'] or 0
                             for b in tx.get('pre_token_balances', [])}

            signer_affected_mints = set()
            signer_wsol_gain = 0
            has_non_signer_drain = False
            is_impure = False

            for post in tx.get('post_token_balances', []):
                acc_idx = post['accountIndex']
                mint = post['mint']
                owner = post.get('owner')

                post_amt = post['uiTokenAmount']['uiAmount'] or 0
                pre_amt = pre_token_map.get(acc_idx, 0)
                if pre_amt is None:
                    pre_amt = 0
                change = post_amt - pre_amt

                if abs(change) < 1e-9:
                    continue

                if owner == signer:
                    # 记录 Signer 变动的所有 Mint
                    signer_affected_mints.add(mint)
                    if mint == wsol_mint:
                        signer_wsol_gain += change
                    else:
                        # 核心修改：只要出现了非 wSOL 的变动，立即判定为“不纯净”
                        is_impure = True
                else:
                    # 来源判定：外部账户扣款
                    if change < -1e-9:
                        has_non_signer_drain = True

            # 3. 严格条件组合判定
            # a) 必须纯净（Signer 只动了 wSOL）
            # b) 必须有外部利润来源 (DEX池子)
            # c) 净利润 > 0
            if not is_impure and has_non_signer_drain:
                net_profit = signer_wsol_gain + sol_change
                if net_profit > 0.00001:
                    found_arbs.append({
                        "slot": slot_id,
                        "sig": tx['sig'],
                        "signer": signer,
                        "sol_spent": abs(sol_change),
                        "wsol_gain": signer_wsol_gain,
                        "profit": net_profit
                    })

    # 结果输出
    if not found_arbs:
        print("\n[结果] 未检测到符合“极致纯净”模式的 SOL 套利。")
    else:
        print(f"\n🎯 发现 {len(found_arbs)} 笔极致纯净套利（Signer 仅持有 wSOL 变动）：")
        for arb in found_arbs:
            print(f"\n" + "💎"*40)
            print(f"交易签名: {arb['sig']}")
            print(f"套利者  : {arb['signer']}")
            print(f"消耗 SOL: {arb['sol_spent']:.6f}")
            print(f"获得 wSOL: {arb['wsol_gain']:.6f}")
            print(f"纯利润  : {arb['profit']:.6f} SOL")
        print("💎"*40 + "\n")


if __name__ == "__main__":
    detect_ultra_pure_sol_arbitrage(
        "mev_full_analysis_324115988_324115989.json")
