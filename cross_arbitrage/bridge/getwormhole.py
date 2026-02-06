import requests
import pandas as pd
from datetime import datetime


def fetch_wormhole_data():
    # 1. Set API URL (pageSize set to 10 so you can view multiple records at once)
    url = "https://api.wormholescan.io/api/v1/operations"

    # Parameter configuration
    params = {
        "page": 0,
        "pageSize": 10,
        "sortOrder": "DESC",
        "appId": "PORTAL_TOKEN_BRIDGE",
        "sourceChain": 1,  # Solana
        "targetChain": 2,  # Ethereum
        # Note: The time range here is set to a broader/more recent window to ensure data is captured.
        # If you want specific historical data, change back to your desired time range.
        "from": "2026-01-12T03:00:00.000Z",
        "to": "2026-01-13T03:00:00.000Z"
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        operations = data.get('operations', [])
        print(f"OK: Successfully fetched {len(operations)} transaction records\n")

        results = []

        for op in operations:
            # === Extract core data fields ===

            # 1. Source chain info (Solana)
            source_tx = op.get('sourceChain', {})
            src_timestamp = source_tx.get('timestamp')  # initiation time
            src_tx_hash = source_tx.get('transaction', {}).get('txHash')

            # 2. Target chain info (Ethereum) - VAA redemption info
            # Note: In the Wormhole API structure, target chain execution info is typically in content -> payload or globalTx,
            # but the operations endpoint usually places completion info directly in 'content' or 'targetChain' (depending on status)

            # If the transaction is not yet completed, there is no targetChain data
            target_tx = op.get('targetChain', {})
            dst_timestamp = target_tx.get('timestamp')  # completion time

            # 3. Status check
            status = op.get('status')  # COMPLETED / IN_PROGRESS

            if status == 'COMPLETED' and src_timestamp and dst_timestamp:
                # Convert time format and calculate latency
                t1 = datetime.fromisoformat(
                    src_timestamp.replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(
                    dst_timestamp.replace('Z', '+00:00'))

                latency_seconds = (t2 - t1).total_seconds()
                latency_minutes = latency_seconds / 60

                results.append({
                    "Tx ID": op.get('id'),
                    "Status": status,
                    "Start Time": t1,
                    "End Time": t2,
                    "Latency (Mins)": round(latency_minutes, 2),
                    "Src Hash": src_tx_hash
                })

        # === Display results ===
        if results:
            df = pd.DataFrame(results)
            print(df[['Status', 'Start Time', 'Latency (Mins)', 'Src Hash']])

            # Calculate average latency
            avg_latency = df['Latency (Mins)'].mean()
            print(f"\nAverage cross-chain latency: {avg_latency:.2f} minutes")
        else:
            print("WARNING: No completed cross-chain transactions found. There may be no data in this time range, or transactions may still be in progress.")

    except Exception as e:
        print(f"Request failed: {e}")


# Run the script
fetch_wormhole_data()
