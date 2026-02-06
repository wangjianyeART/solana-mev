# Kamino Liquidation Profit Analysis Tool

Automatically analyzes the profit of Kamino Lending protocol liquidation transactions.

**Features:**

- Automatically fetches transaction data (Helius API)
- Automatically parses Kamino instructions (IDL)
- **Automatically fetches historical SOL prices (Binance K-lines)**
- Automatically calculates liquidation profit

## File Structure

```
liquidation_analyzer/
├── kamino_liquidation_analyzer.py  # All-in-one analysis tool
├── 01_fetch_transaction.py         # Step 1: Fetch transaction
├── 02_parse_transaction.py         # Step 2: Parse transaction
├── 03_calculate_profit.py          # Step 3: Calculate profit
├── kamino_decoder.py               # Kamino IDL decoder
├── kamino_lending_idl.json         # Kamino Lending IDL
├── .env                            # API configuration (Helius API Key)
└── data/                           # Data output directory
```

## Configuration

Edit the `.env` file and fill in your Helius API Key:

```
HELIUS_API_KEY=your_api_key_here
```

Free registration: https://helius.dev

## Usage

### Analyze a transaction (automatically fetches price)

```bash
python kamino_liquidation_analyzer.py <transaction_signature>
```

The program will **automatically fetch the SOL price at the time of the transaction from Binance**, no need to specify manually.

### Example

```bash
python kamino_liquidation_analyzer.py 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg
```

### Manually specify price (optional)

If you need to use a specific price, add the `--sol-price` parameter:

```bash
python kamino_liquidation_analyzer.py <transaction_signature> --sol-price 204.92
```

### Manual calculation mode

No transaction signature needed, directly input values for calculation:

```bash
python kamino_liquidation_analyzer.py --manual \
    --collateral 0.05479 \
    --protocol-fee 0.0013 \
    --priority-fee 0.001317 \
    --debt 10.642 \
    --sol-price 204.92
```

## Step-by-step execution (optional)

```bash
# Step 1: Fetch transaction
python 01_fetch_transaction.py <signature> ./data/tx_raw.json

# Step 2: Parse transaction
python 02_parse_transaction.py ./data/tx_raw.json

# Step 3: Calculate profit
python 03_calculate_profit.py ./data/tx_raw_parsed.json
```

## Profit formula

```
Net profit = (Net SOL x SOL price) - Repaid debt
Net SOL    = Liquidation proceeds - Protocol fee - Priority fee
```

## Output example

```
Step 1: Calculate net SOL
   Total collateral:  0.054790717 SOL
   - Protocol fee:    0.001304541 SOL
   - Priority fee:    0.001317996 SOL
   ---------------------------------
   Net SOL =          0.052168180 SOL

Step 2: Convert to USD
   0.052168180 SOL x $204.31 = $10.6585

Step 3: Calculate net profit
   Total revenue (USD):    $10.6585
   - Repaid debt:          $10.642299 USDC
   ---------------------------------
   Net profit =            $0.0162 USD
```

## Dependencies

- Python 3.7+
- No additional packages required (uses standard library)
- Helius API Key
