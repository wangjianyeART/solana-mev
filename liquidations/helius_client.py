"""
Helius API Client - Fetch enhanced transaction data
"""
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import config
from liquidation_parser import LiquidationParser


class HeliusClient:
    """Helius API Client"""

    def __init__(self, api_key: str):
        """
        Initialize the client

        Args:
            api_key: Helius API key
        """
        self.api_key = api_key
        self.base_url = config.HELIUS_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def get_address_transactions(
        self,
        address: str,
        limit: int = 100,
        before: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get transaction history for a given address

        Args:
            address: Program address
            limit: Return count limit
            before: Transactions before this signature
            until: Transactions after this signature

        Returns:
            List of transactions
        """
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{self.base_url}/addresses/{address}/transactions"
        params = {
            "api-key": self.api_key,
            "limit": limit,
        }

        if before:
            params["before"] = before
        if until:
            params["until"] = until

        try:
            async with self.session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data if isinstance(data, list) else []
                elif response.status == 429:
                    print(f"Rate limited: waiting before retry...")
                    await asyncio.sleep(config.HELIUS_RATE_LIMIT_DELAY * 5)
                    return []
                else:
                    print(f"Request failed: {response.status}, {await response.text()}")
                    return []
        except asyncio.TimeoutError:
            print(f"Request timeout: {address}")
            return []
        except Exception as e:
            print(f"Request error: {e}")
            return []

    async def get_transactions_in_time_range(
        self,
        address: str,
        hours: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get transactions within a specified time range

        Args:
            address: Program address
            hours: Time range (in hours)

        Returns:
            List of transactions
        """
        cutoff_time = datetime.now() - timedelta(hours=hours)
        cutoff_timestamp = int(cutoff_time.timestamp())

        all_transactions = []
        before_signature = None
        max_pages = 10  # Limit max pages to avoid infinite loops

        for page in range(max_pages):
            print(f"  Querying page {page + 1}...")

            transactions = await self.get_address_transactions(
                address=address,
                limit=100,
                before=before_signature,
            )

            if not transactions:
                break

            # Filter transactions within the time range
            valid_transactions = []
            for tx in transactions:
                tx_timestamp = tx.get("timestamp", 0)
                if tx_timestamp >= cutoff_timestamp:
                    valid_transactions.append(tx)
                else:
                    # Already past the time range, stop querying
                    return all_transactions + valid_transactions

            all_transactions.extend(valid_transactions)

            # If fewer results than the limit, no more data available
            if len(transactions) < 100:
                break

            # Set the starting position for the next page
            before_signature = transactions[-1].get("signature")

            # Rate limiting
            await asyncio.sleep(config.HELIUS_RATE_LIMIT_DELAY)

        return all_transactions

    async def get_liquidations_for_protocol(
        self,
        protocol_name: str,
        protocol_address: str,
        hours: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get liquidation transactions for a specific protocol

        Args:
            protocol_name: Protocol name
            protocol_address: Protocol program address
            hours: Time range (in hours)

        Returns:
            List of liquidation events
        """
        print(f"\nQuerying transactions for {protocol_name}...")

        # Get all transactions within the time range
        transactions = await self.get_transactions_in_time_range(
            address=protocol_address,
            hours=hours,
        )

        print(f"  Found {len(transactions)} transactions")

        # Filter liquidation transactions
        parser = LiquidationParser()
        liquidations = []

        for tx in transactions:
            if parser.is_liquidation_tx(tx):
                event = parser.parse_helius_transaction(tx, protocol_name)
                if event:
                    liquidations.append(event)

        print(f"  Identified {len(liquidations)} liquidation transactions")
        return liquidations

    async def get_all_liquidations(self, hours: int = 1) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get liquidation transactions for all protocols

        Args:
            hours: Time range (in hours)

        Returns:
            Liquidation events grouped by protocol
        """
        results = {}

        # Query all protocols concurrently
        tasks = []
        for protocol_name, protocol_address in config.LENDING_PROTOCOLS.items():
            task = self.get_liquidations_for_protocol(
                protocol_name, protocol_address, hours
            )
            tasks.append((protocol_name, task))

        # Wait for all tasks to complete
        for protocol_name, task in tasks:
            liquidations = await task
            results[protocol_name] = liquidations

        return results
