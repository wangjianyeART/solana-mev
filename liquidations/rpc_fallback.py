"""
RPC Fallback - Use raw Solana RPC when Helius API is unavailable
"""
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.signature import Signature
import config
from liquidation_parser import LiquidationParser


class RPCFallbackClient:
    """Solana RPC Fallback Client"""

    def __init__(self, rpc_url: str):
        """
        Initialize the client

        Args:
            rpc_url: Solana RPC URL
        """
        self.rpc_url = rpc_url
        self.client: Optional[AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.client = AsyncClient(self.rpc_url)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.client:
            await self.client.close()

    async def get_signatures_for_address(
        self,
        address: str,
        limit: int = 100,
        before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get signature list for a given address

        Args:
            address: Program address
            limit: Return count limit
            before: Before this signature

        Returns:
            List of signatures
        """
        if not self.client:
            self.client = AsyncClient(self.rpc_url)

        try:
            # Convert string address to Pubkey
            from solders.pubkey import Pubkey
            pubkey = Pubkey.from_string(address)

            options = {"limit": limit}
            if before:
                options["before"] = Signature.from_string(before)

            response = await self.client.get_signatures_for_address(pubkey, **options)

            if response.value:
                return [
                    {
                        "signature": str(sig.signature),
                        "slot": sig.slot,
                        "blockTime": sig.block_time,
                        "err": sig.err,
                    }
                    for sig in response.value
                ]
            return []

        except Exception as e:
            print(f"Failed to get signatures: {e}")
            return []

    async def get_transaction(self, signature: str) -> Optional[Dict[str, Any]]:
        """
        Get transaction details

        Args:
            signature: Transaction signature

        Returns:
            Transaction data
        """
        if not self.client:
            self.client = AsyncClient(self.rpc_url)

        try:
            sig = Signature.from_string(signature)
            response = await self.client.get_transaction(
                sig,
                encoding="jsonParsed",
                max_supported_transaction_version=0,
            )

            if response.value:
                return {
                    "signature": signature,
                    "blockTime": response.value.block_time,
                    "meta": response.value.transaction.meta,
                    "transaction": response.value.transaction.transaction,
                }
            return None

        except Exception as e:
            print(f"Failed to get transaction {signature}: {e}")
            return None

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
        print(f"\n[RPC Fallback] Querying transactions for {protocol_name}...")

        cutoff_time = datetime.now() - timedelta(hours=hours)
        cutoff_timestamp = int(cutoff_time.timestamp())

        all_signatures = []
        before_sig = None
        max_pages = 5  # RPC is slower, limit pages

        # Get signature list
        for page in range(max_pages):
            print(f"  Querying signatures page {page + 1}...")
            signatures = await self.get_signatures_for_address(
                address=protocol_address,
                limit=100,
                before=before_sig,
            )

            if not signatures:
                break

            # Filter time range
            valid_sigs = []
            for sig_info in signatures:
                block_time = sig_info.get("blockTime", 0)
                if block_time and block_time >= cutoff_timestamp:
                    valid_sigs.append(sig_info)
                else:
                    # Past the time range
                    all_signatures.extend(valid_sigs)
                    break

            if len(valid_sigs) < len(signatures):
                # Reached the time boundary
                break

            all_signatures.extend(valid_sigs)

            if len(signatures) < 100:
                break

            before_sig = signatures[-1]["signature"]
            await asyncio.sleep(0.1)  # Avoid rate limiting

        print(f"  Found {len(all_signatures)} signatures")

        # Get transaction details and filter liquidations
        parser = LiquidationParser()
        liquidations = []

        for i, sig_info in enumerate(all_signatures):
            if i % 10 == 0:
                print(f"  Processing transaction {i}/{len(all_signatures)}...")

            signature = sig_info["signature"]
            tx_data = await self.get_transaction(signature)

            if tx_data:
                # Check if this is a liquidation transaction (simple log check)
                meta = tx_data.get("meta", {})
                logs = meta.get("logMessages", [])

                # Check if logs contain liquidation keywords
                is_liquidation = any(
                    any(keyword in log for keyword in config.LIQUIDATION_KEYWORDS)
                    for log in logs
                )

                if is_liquidation:
                    event = parser.parse_rpc_transaction(tx_data, protocol_name)
                    if event:
                        liquidations.append(event)

            await asyncio.sleep(0.05)  # RPC request interval

        print(f"  Identified {len(liquidations)} liquidation transactions")
        return liquidations
