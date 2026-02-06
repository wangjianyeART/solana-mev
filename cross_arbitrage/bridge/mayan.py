import requests
import pandas as pd


def get_mayan_supported_tokens():
    # Mayan Finance public API endpoint
    url = "https://api.mayan.finance/v3/tokens"

    try:
        response = requests.get(url)
        data = response.json()

        # Filtering logic:
        # 1. Find tokens on the Solana chain (Mayan's chainId for Solana is usually 'solana' or a numeric identifier)
        # 2. The API returns a dictionary of all supported tokens

        sol_tokens = []

        for token_symbol, token_info in data.items():
            # Check if this token is supported on Solana
            if token_info.get('chain') == 'solana':
                # Record the information we need
                sol_tokens.append({
                    "Symbol": token_info.get('symbol'),
                    "Name": token_info.get('name'),
                    "Contract Address": token_info.get('mint'),
                    "Decimals": token_info.get('decimals')
                })

        # Convert to DataFrame for display
        df = pd.DataFrame(sol_tokens)
        print(f"OK: Mayan protocol supports {len(df)} assets originating from Solana")
        print(df.head(10))  # print the first 10
        return df

    except Exception as e:
        print(f"Error fetching data: {e}")


# Execute
get_mayan_supported_tokens()
