"""
Liquidation Transaction Parser - Extract liquidation events from transaction data
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
import config


@dataclass
class LiquidationEvent:
    """Liquidation event data structure"""
    protocol: str
    tx_signature: str
    timestamp: datetime
    liquidator: str  # Liquidator address
    liquidatee: str  # Liquidatee address
    debt_token: str  # Debt token
    debt_amount: float  # Debt amount
    collateral_token: str  # Collateral token
    collateral_amount: float  # Collateral amount
    tx_fee: float  # Transaction fee (SOL)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        return result


class LiquidationParser:
    """Liquidation Transaction Parser"""

    @staticmethod
    def is_liquidation_tx(tx_data: Dict[str, Any]) -> bool:
        """
        Determine if a transaction is a liquidation transaction

        Args:
            tx_data: Transaction data (Helius Enhanced format)

        Returns:
            Whether it is a liquidation transaction
        """
        # Check transaction type
        if tx_data.get("type") == "LIQUIDATE":
            return True

        # Check instruction descriptions
        instructions = tx_data.get("instructions", [])
        for instruction in instructions:
            # Helius parsed instruction name
            if "parsed" in instruction:
                parsed = instruction["parsed"]
                if isinstance(parsed, dict):
                    instruction_name = parsed.get("type", "")
                    if any(keyword in instruction_name for keyword in config.LIQUIDATION_KEYWORDS):
                        return True

        # Check log messages
        logs = tx_data.get("logs", [])
        for log in logs:
            if any(keyword in log for keyword in config.LIQUIDATION_KEYWORDS):
                return True

        # Check description
        description = tx_data.get("description", "")
        if any(keyword in description for keyword in config.LIQUIDATION_KEYWORDS):
            return True

        return False

    @staticmethod
    def parse_helius_transaction(tx_data: Dict[str, Any], protocol: str) -> Optional[LiquidationEvent]:
        """
        Parse Helius Enhanced Transaction data

        Args:
            tx_data: Helius enhanced transaction data
            protocol: Protocol name

        Returns:
            LiquidationEvent or None
        """
        try:
            # Basic info
            signature = tx_data.get("signature", "")
            timestamp_sec = tx_data.get("timestamp", 0)
            timestamp = datetime.fromtimestamp(timestamp_sec)

            # Fee
            fee = tx_data.get("fee", 0) / 1e9  # lamports to SOL

            # Account info
            accounts = tx_data.get("accountData", [])
            fee_payer = tx_data.get("feePayer", "")

            # Token transfer info
            token_transfers = tx_data.get("tokenTransfers", [])
            native_transfers = tx_data.get("nativeTransfers", [])

            # Try to identify the liquidator and liquidatee
            liquidator = fee_payer  # Usually the liquidator is the transaction initiator
            liquidatee = ""

            # Try to identify from token transfers
            debt_token = ""
            debt_amount = 0.0
            collateral_token = ""
            collateral_amount = 0.0

            if len(token_transfers) >= 2:
                # The first transfer is usually debt repayment (from liquidator to protocol)
                debt_transfer = token_transfers[0]
                debt_token = debt_transfer.get("mint", "")
                debt_amount = debt_transfer.get("tokenAmount", 0)

                # The second transfer is usually collateral received (from protocol to liquidator)
                collateral_transfer = token_transfers[1]
                collateral_token = collateral_transfer.get("mint", "")
                collateral_amount = collateral_transfer.get("tokenAmount", 0)

                # Try to find the liquidatee from transfer info
                for transfer in token_transfers:
                    from_addr = transfer.get("fromUserAccount", "")
                    to_addr = transfer.get("toUserAccount", "")
                    if from_addr and from_addr != liquidator and from_addr != config.LENDING_PROTOCOLS.get(protocol, ""):
                        liquidatee = from_addr
                        break

            # If no explicit liquidatee found, use placeholder
            if not liquidatee:
                liquidatee = "Unknown"

            return LiquidationEvent(
                protocol=protocol,
                tx_signature=signature,
                timestamp=timestamp,
                liquidator=liquidator,
                liquidatee=liquidatee,
                debt_token=debt_token,
                debt_amount=debt_amount,
                collateral_token=collateral_token,
                collateral_amount=collateral_amount,
                tx_fee=fee,
            )

        except Exception as e:
            print(f"Failed to parse transaction {tx_data.get('signature', 'unknown')}: {e}")
            return None

    @staticmethod
    def parse_rpc_transaction(tx_data: Dict[str, Any], protocol: str) -> Optional[LiquidationEvent]:
        """
        Parse raw RPC transaction data

        Args:
            tx_data: Solana RPC getTransaction response data
            protocol: Protocol name

        Returns:
            LiquidationEvent or None
        """
        try:
            # TODO: Implement raw RPC data parsing
            # This requires parsing raw instruction data and logs
            # Used when Helius API is unavailable

            signature = tx_data.get("signature", "")
            block_time = tx_data.get("blockTime", 0)
            timestamp = datetime.fromtimestamp(block_time) if block_time else datetime.now()

            # Simplified version: only return basic info
            return LiquidationEvent(
                protocol=protocol,
                tx_signature=signature,
                timestamp=timestamp,
                liquidator="Unknown",
                liquidatee="Unknown",
                debt_token="Unknown",
                debt_amount=0.0,
                collateral_token="Unknown",
                collateral_amount=0.0,
                tx_fee=0.0,
            )

        except Exception as e:
            print(f"Failed to parse RPC transaction: {e}")
            return None
