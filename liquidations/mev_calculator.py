"""
MEV Profit Calculator - Calculate liquidation MEV profit
"""
import asyncio
import aiohttp
from typing import Dict, Optional
import config
from liquidation_parser import LiquidationEvent


class MEVCalculator:
    """MEV Profit Calculator"""

    def __init__(self):
        """Initialize the calculator"""
        self.price_cache: Dict[str, float] = {}
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def get_token_price(self, mint: str) -> float:
        """
        Get token price (USD)

        Args:
            mint: Token mint address

        Returns:
            Price (USD)
        """
        # Check cache
        if mint in self.price_cache:
            return self.price_cache[mint]

        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            # Use Jupiter Price API
            url = f"{config.JUPITER_PRICE_API}?ids={mint}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    price_data = data.get("data", {}).get(mint, {})
                    price = price_data.get("price", 0.0)

                    # Cache the price
                    self.price_cache[mint] = price
                    return price
                else:
                    print(f"Failed to get price for {mint}: {response.status}")
                    return 0.0

        except Exception as e:
            print(f"Price fetch error for {mint}: {e}")
            return 0.0

    async def calculate_mev_profit(self, event: LiquidationEvent) -> Dict[str, float]:
        """
        Calculate MEV profit for a liquidation

        Args:
            event: Liquidation event

        Returns:
            Dictionary containing various metrics
        """
        # Get token prices
        debt_price = 0.0
        collateral_price = 0.0

        if event.debt_token and event.debt_token != "Unknown":
            debt_price = await self.get_token_price(event.debt_token)

        if event.collateral_token and event.collateral_token != "Unknown":
            collateral_price = await self.get_token_price(event.collateral_token)

        # Calculate values
        debt_value_usd = event.debt_amount * debt_price
        collateral_value_usd = event.collateral_amount * collateral_price

        # Calculate MEV profit
        gross_profit = collateral_value_usd - debt_value_usd
        net_profit = gross_profit - (event.tx_fee * 100)  # Assuming SOL = $100

        # Calculate ROI
        roi = (gross_profit / debt_value_usd * 100) if debt_value_usd > 0 else 0.0

        # Calculate liquidation discount
        liquidation_discount = (
            (collateral_value_usd - debt_value_usd) / debt_value_usd * 100
            if debt_value_usd > 0
            else 0.0
        )

        return {
            "debt_value_usd": debt_value_usd,
            "collateral_value_usd": collateral_value_usd,
            "gross_profit_usd": gross_profit,
            "net_profit_usd": net_profit,
            "roi_percent": roi,
            "liquidation_discount_percent": liquidation_discount,
            "tx_fee_usd": event.tx_fee * 100,  # Assuming SOL = $100
        }

    async def enrich_liquidation_events(
        self, events: list[LiquidationEvent]
    ) -> list[Dict]:
        """
        Add MEV calculation results to liquidation events

        Args:
            events: List of liquidation events

        Returns:
            List of dictionaries with MEV data
        """
        enriched = []

        for event in events:
            mev_data = await self.calculate_mev_profit(event)

            result = event.to_dict()
            result.update(mev_data)
            enriched.append(result)

        return enriched
