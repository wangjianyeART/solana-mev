"""
Configuration file - API keys and protocol addresses
"""
import os
from typing import Dict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_BASE_URL = "https://api.helius.xyz/v0"

# Solana RPC configuration
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Jupiter Price API
JUPITER_PRICE_API = "https://api.jup.ag/price/v2"

# Lending protocol program addresses
LENDING_PROTOCOLS: Dict[str, str] = {
    "Solend": "So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo",
    "MarginFi": "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
    "Kamino": "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
    "Mango": "4MangoMjqJ2firMokCjjGgoK8d4MXcrgL7XJaL3w6fVg",
}

# Time configuration (seconds)
DEFAULT_TIME_RANGE = 3600  # 1 hour

# API limits
HELIUS_RATE_LIMIT_DELAY = 0.2  # seconds
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30  # seconds

# Liquidation keywords
LIQUIDATION_KEYWORDS = [
    "liquidate",
    "Liquidate",
    "LIQUIDATE",
    "liquidation",
    "Liquidation",
]
