"""
Based on batch_detect arbitrage detection, compute profit and transaction chain length for each arbitrage,
outputting data for "profit vs chain length" research.
Data source: a local single JSON file (format: { "slot_id": [ tx, ... ], ... }).
"""
import json
import os
import sys

sys.stdout.reconfigure(line_buffering=True)

# Local JSON path: prefer command line argument, otherwise use default path below
DEFAULT_INPUT_JSON = os.path.join(os.path.dirname(
    __file__), "arb_324115988_324115990.json")
OUTPUT_JSON = os.path.join(os.path.dirname(
    __file__), "arb_profit_chain_length.json")
OUTPUT_CSV = os.path.join(os.path.dirname(
    __file__), "arb_profit_chain_length.csv")


def _get_num_signers_from_tx(tx):
    """Get number of signers from raw transaction: prefer tx.num_signers (written by gettxfromslot), otherwise parse message.header."""
    if tx.get("num_signers") is not None:
        return int(tx["num_signers"])
    msg = tx.get("message") or {}
    if isinstance(msg, dict):
        n = (msg.get("header") or {}).get("numRequiredSignatures")
        if n is not None:
            return int(n)
    return 1


def main():
    input_path = (sys.argv[1].strip() if len(sys.argv)
                  > 1 else None) or DEFAULT_INPUT_JSON
    if not os.path.exists(input_path):
        print(f"Error: file not found {input_path}")
        print("Usage: python compute_profit_chain.py [path/to/xxx.json]")
        return

    print(f"Data source: {input_path}\n")

    with open(input_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"Parse error: {e}")
            return

    from detect_arbitrage import (
        USDC_MINT,
        USDT_MINT,
        detect_sol_arbitrage,
        detect_stablecoin_arbitrage,
    )

    rows = []
    slot_range = os.path.splitext(os.path.basename(input_path))[0]

    for slot_id, transactions in data.items():
        for tx in transactions:
            # Get number of signers from raw tx (written by gettxfromslot); fallback to detection default
            num_signers = _get_num_signers_from_tx(tx)
            sol_arb = detect_sol_arbitrage(tx, slot_id)
            if sol_arb:
                rows.append({
                    "slot_range": slot_range,
                    "slot": sol_arb["slot"],
                    "sig": sol_arb["sig"],
                    "type": sol_arb["type"],
                    "profit": sol_arb["profit"],
                    "profit_unit": sol_arb["profit_unit"],
                    "chain_length": sol_arb.get("chain_length", 0),
                    "num_signers": num_signers,
                })
            usdc_arb = detect_stablecoin_arbitrage(
                tx, slot_id, USDC_MINT, "USDC")
            if usdc_arb:
                rows.append({
                    "slot_range": slot_range,
                    "slot": usdc_arb["slot"],
                    "sig": usdc_arb["sig"],
                    "type": usdc_arb["type"],
                    "profit": usdc_arb["profit"],
                    "profit_unit": usdc_arb["profit_unit"],
                    "chain_length": usdc_arb.get("chain_length", 0),
                    "num_signers": num_signers,
                })
            usdt_arb = detect_stablecoin_arbitrage(
                tx, slot_id, USDT_MINT, "USDT")
            if usdt_arb:
                rows.append({
                    "slot_range": slot_range,
                    "slot": usdt_arb["slot"],
                    "sig": usdt_arb["sig"],
                    "type": usdt_arb["type"],
                    "profit": usdt_arb["profit"],
                    "profit_unit": usdt_arb["profit_unit"],
                    "chain_length": usdt_arb.get("chain_length", 0),
                    "num_signers": num_signers,
                })

    print(f"Processing complete, arbitrage count: {len(rows)}")

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {OUTPUT_JSON}")

    # Save CSV (convenient for regression/plotting)
    with open(OUTPUT_CSV, "w", encoding="utf-8") as f:
        f.write(
            "slot_range,slot,sig,type,profit,profit_unit,chain_length,num_signers\n")
        for r in rows:
            f.write(
                f"{r['slot_range']},{r['slot']},{r['sig']},{r['type']},{r['profit']},{r['profit_unit']},{r['chain_length']},{r['num_signers']}\n"
            )
    print(f"Saved: {OUTPUT_CSV}")

    # Brief statistics
    if rows:
        profits = [r["profit"] for r in rows]
        chains = [r["chain_length"] for r in rows]
        print(f"\nArbitrage count: {len(rows)}")
        print(f"Profit (by type): SOL={sum(r['profit'] for r in rows if r['type']=='SOL'):.6f}, "
              f"USDC={sum(r['profit'] for r in rows if r['type']=='USDC'):.4f}, "
              f"USDT={sum(r['profit'] for r in rows if r['type']=='USDT'):.4f}")
        print(
            f"chain_length: min={min(chains):.2f}, max={max(chains):.2f}, mean={sum(chains)/len(chains):.2f}")


if __name__ == "__main__":
    main()
