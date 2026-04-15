# APEX — OnChain Data Router
# Mock mint addresses → MockOnChainFetcher
# Real Solana mint addresses → HeliusClient

import logging
from data.mock_feeds import MockOnChainFetcher
from data.helius_client import HeliusClient

logger = logging.getLogger("apex.onchain")

HELIUS_API_KEY = "d9be6ae5-bafe-4c2c-ba10-211c6e2dabfa"

def is_real_mint(mint_address: str) -> bool:
    """
    Real Solana mint addresses are base58, 32-44 chars.
    Mock addresses start with 'mock_'.
    """
    return (
        not mint_address.startswith("mock_") and
        len(mint_address) >= 32
    )


class OnChainRouter:
    """
    Drop-in replacement for both MockOnChainFetcher and HeliusClient.
    Routes requests based on mint address type.
    """

    def __init__(self):
        self._mock   = MockOnChainFetcher()
        self._helius = HeliusClient(HELIUS_API_KEY)

    async def get_volume_data(self, mint_address: str, scenario: str = "") -> dict:
        if is_real_mint(mint_address):
            logger.info(f"[ROUTER] Real mint → Helius: {mint_address[:12]}...")
            return await self._helius.get_volume_data(mint_address)
        else:
            logger.debug(f"[ROUTER] Mock mint → MockFetcher: {mint_address}")
            return await self._mock.get_volume_data(mint_address, scenario)

    async def check_honeypot(self, mint_address: str) -> bool:
        if is_real_mint(mint_address):
            return await self._helius.check_honeypot(mint_address)
        return await self._mock.check_honeypot(mint_address)

    async def get_dev_wallet_pct(self, mint_address: str) -> float:
        if is_real_mint(mint_address):
            return await self._helius.get_dev_wallet_pct(mint_address)
        return await self._mock.get_dev_wallet_pct(mint_address)
