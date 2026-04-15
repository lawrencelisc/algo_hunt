# Quick Helius connection test
import asyncio, sys, json
sys.path.insert(0, '.')
from data.helius_client import HeliusClient

API_KEY = "d9be6ae5-bafe-4c2c-ba10-211c6e2dabfa"

async def main():
    client = HeliusClient(API_KEY)

    print("=== HELIUS CONNECTION TEST ===")

    # Test 1: Basic health
    ok = client.test_connection()
    print(f"Health check: {'✅ OK' if ok else '❌ FAIL'}")

    # Test 2: SOL price
    price = client._get_sol_price()
    print(f"SOL price:    ${price:.2f}")

    # Test 3: Known token (USDC on Solana)
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    print(f"\nTesting token lookup: USDC")
    honeypot = await client.check_honeypot(USDC_MINT)
    print(f"Honeypot check: {'⚠️ YES' if honeypot else '✅ CLEAN'}")

    dev_pct = await client.get_dev_wallet_pct(USDC_MINT)
    print(f"Largest holder: {dev_pct:.1%}")

    print("\n✅ Helius client ready for integration")

asyncio.run(main())
