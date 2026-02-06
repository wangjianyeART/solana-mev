import json
import os


def detect_usdt_arbitrage(file_path):
    if not os.path.exists(file_path):
        print(f"错误: 找不到文件 {file_path}")
        return

    print(f"正在分析 usdt 套利交易: {file_path} ...")
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"解析错误: {e}")
            return

    usdt_mint = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

    for slot_id, transactions in data.items():
        found_in_slot = 0
        for tx in transactions:
            if tx.get('has_err'):
                continue

            signer = tx['accounts'][0]
            signer_usdt_profit = 0
            pool_increases = []

            # 建立 Pre 余额映射，用于计算差额
            pre_balances = {b['accountIndex']: float(b['uiTokenAmount']['uiAmount'] or 0)
                            for b in tx.get('pre_token_balances', [])}

            # 遍历 Post 余额
            for balance in tx.get('post_token_balances', []):
                # 仅锁定 usdt Mint
                if balance['mint'] != usdt_mint:
                    continue

                acc_idx = balance['accountIndex']
                owner = balance.get('owner')
                post_amt = float(balance['uiTokenAmount']['uiAmount'] or 0)
                pre_amt = pre_balances.get(acc_idx, 0.0)

                diff = post_amt - pre_amt

                # 逻辑核心：区分 Signer 和 池子
                if owner == signer:
                    signer_usdt_profit += diff
                elif diff > 0:
                    # 记录非 Signer 账户（池子）收到的 usdt 增加量
                    pool_increases.append(diff)

            # --- 核心判定逻辑 ---
            # 1. 必须有池子变动，且 Signer 必须有正利润
            if not pool_increases or signer_usdt_profit <= 1e-6:
                continue

            max_pool_inc = max(pool_increases)

            # 2. 计算杠杆率 (Pool增量 / Signer利润)
            # 在套利中，Pool 增加的是整笔交易的本金，Signer 增加的是微小的差价
            if max_pool_inc > signer_usdt_profit * 1:
                leverage = signer_usdt_profit / max_pool_inc
                found_in_slot += 1

                print(f"\n" + "💰"*20)
                print(f"【发现 usdt 套利】")
                print(f"Slot      : {slot_id}")
                print(f"签名 (Sig) : {tx['sig']}")
                print(f"套利者    : {signer}")
                print(f"套利净利  : {signer_usdt_profit:.4f} usdt")
                print(f"池子流水  : {max_pool_inc:.4f} usdt")
                print(f"利润率  : {leverage:.5f}%")
                print("💰"*20)

        if found_in_slot == 0:
            print(f"Slot {slot_id}: 未发现 usdt 套利交易。")


if __name__ == "__main__":
    file_name = "mev_full_analysis_252308623_252308633.json"
    detect_usdt_arbitrage(file_name)
