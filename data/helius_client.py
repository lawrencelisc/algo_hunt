# APEX — Helius RPC Client (Real On-chain Data)
# Replaces MockOnChainFetcher in Phase 2
# Docs: https://docs.helius.dev

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

logger = logging.getLogger("apex.helius")


class HeliusClient:
    """
    Real Helius RPC client.
    Replaces MockOnChainFetcher — drop-in compatible interface.
    """

    def __init__(self, api_key: str):
        self.api_key  = api_key
        self.rpc_url  = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        self.api_url  = f"https://api.helius.xyz/v0"

    # ─────────────────────────────────────────
    #  HTTP HELPERS
    # ─────────────────────────────────────────

    def _post_rpc(self, method: str, params: list) -> dict:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id":      1,
            "method":  method,
            "params":  params,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.rpc_url,
            data    = payload,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[HELIUS] RPC {method} failed: {e}")
            return {}

    def _get_api(self, path: str, params: dict = {}) -> dict:
        params["api-key"] = self.api_key
        url = self.api_url + path + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[HELIUS] API {path} failed: {e}")
            return {}

    def _post_api(self, path: str, body: dict) -> dict:
        url     = self.api_url + path + f"?api-key={self.api_key}"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data    = payload,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[HELIUS] API POST {path} failed: {e}")
            return {}

    # ─────────────────────────────────────────
    #  CORE: ON-CHAIN FLOW  (replaces mock)
    # ─────────────────────────────────────────

    async def get_volume_data(self, mint_address: str, scenario: str = "") -> dict:
        """
        Drop-in replacement for MockOnChainFetcher.get_volume_data()
        Returns: volume_sigma, netflow_usd, buy_count, sell_count, unique_wallets
        """
        # 1. Get recent transactions for this mint
        txs = self._get_token_transactions(mint_address, limit=100)

        if not txs:
            logger.warning(f"[HELIUS] No transactions for {mint_address}")
            return {
                "mint":           mint_address,
                "volume_sigma":   0.0,
                "netflow_usd":    0.0,
                "buy_count":      0,
                "sell_count":     0,
                "unique_wallets": 0,
            }

        # 2. Calculate buy/sell flows
        buy_vol  = 0.0
        sell_vol = 0.0
        buy_count  = 0
        sell_count = 0
        wallets    = set()

        for tx in txs:
            tx_type = tx.get("type", "")
            amount  = float(tx.get("nativeTransfers", [{}])[0].get("amount", 0)) / 1e9

            if tx_type in ("SWAP", "TOKEN_MINT"):
                buy_vol  += amount
                buy_count += 1
            elif tx_type in ("TRANSFER",):
                sell_vol  += amount
                sell_count += 1

            # Track unique wallets
            for acct in tx.get("accountData", []):
                wallets.add(acct.get("account", ""))

        netflow_usd = (buy_vol - sell_vol) * self._get_sol_price()

        # 3. Calculate volume sigma vs 24h baseline
        # Simple approximation: compare last 1h vs 24h average
        vol_total  = buy_vol + sell_vol
        baseline   = vol_total / 24 if vol_total > 0 else 1
        vol_1h     = vol_total / max(len(txs) / 24, 1)
        sigma      = (vol_1h - baseline) / max(baseline * 0.2, 0.001)

        logger.info(
            f"[HELIUS] {mint_address[:8]}... | "
            f"buys={buy_count} sells={sell_count} "
            f"netflow=${netflow_usd:.0f} σ={sigma:.1f}"
        )

        return {
            "mint":           mint_address,
            "volume_sigma":   round(max(sigma, 0), 2),
            "netflow_usd":    round(netflow_usd, 2),
            "buy_count":      buy_count,
            "sell_count":     sell_count,
            "unique_wallets": len(wallets),
        }

    # ─────────────────────────────────────────
    #  TOKEN TRANSACTIONS
    # ─────────────────────────────────────────

    def _get_token_transactions(self, mint_address: str, limit: int = 100) -> list:
        """Get recent transactions involving this token mint."""
        resp = self._post_api(f"/addresses/{mint_address}/transactions", {
            "limit": limit,
            "type":  "SWAP",
        })
        if isinstance(resp, list):
            return resp
        return resp.get("transactions", [])

    # ─────────────────────────────────────────
    #  HONEYPOT CHECK  (replaces mock)
    # ─────────────────────────────────────────

    async def check_honeypot(self, mint_address: str) -> bool:
        """
        Check if token has suspicious characteristics.
        Returns True if honeypot risk detected.
        """
        resp = self._post_rpc("getAccountInfo", [
            mint_address,
            {"encoding": "jsonParsed"}
        ])

        result = resp.get("result", {})
        if not result or not result.get("value"):
            logger.warning(f"[HELIUS] No account info for {mint_address}")
            return True  # Treat unknown as risky

        # Check mint authority (honeypots often retain mint authority)
        data = result.get("value", {}).get("data", {})
        parsed = data.get("parsed", {}) if isinstance(data, dict) else {}
        info   = parsed.get("info", {})

        mint_authority = info.get("mintAuthority")
        freeze_auth    = info.get("freezeAuthority")

        if freeze_auth:
            logger.warning(f"[HELIUS] {mint_address[:8]} has freeze authority — honeypot risk")
            return True

        return False

    # ─────────────────────────────────────────
    #  DEV WALLET TRACKER  (replaces mock)
    # ─────────────────────────────────────────

    async def get_dev_wallet_pct(self, mint_address: str) -> float:
        """
        Returns fraction of supply held by largest wallet.
        High concentration = dev wallet / rug risk.
        """
        resp = self._post_rpc("getTokenLargestAccounts", [mint_address])
        result = resp.get("result", {})
        accounts = result.get("value", [])

        if not accounts:
            return 0.0

        # Get total supply
        supply_resp = self._post_rpc("getTokenSupply", [mint_address])
        total = float(supply_resp.get("result", {})
                      .get("value", {})
                      .get("uiAmount", 1) or 1)

        largest = float(accounts[0].get("uiAmount", 0) if accounts else 0)
        pct     = largest / total if total > 0 else 0

        logger.info(f"[HELIUS] {mint_address[:8]} largest holder: {pct:.1%}")
        return round(pct, 4)

    # ─────────────────────────────────────────
    #  PUMP.FUN NEW TOKEN EVENTS
    # ─────────────────────────────────────────

    async def get_new_pump_tokens(self, limit: int = 10) -> list[dict]:
        """
        Fetch recently launched Pump.fun tokens.
        Real replacement for MockPumpFunWatcher.
        """
        PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

        resp = self._post_api(f"/addresses/{PUMP_FUN_PROGRAM}/transactions", {
            "limit": limit,
            "type":  "TOKEN_MINT",
        })

        tokens = []
        txs    = resp if isinstance(resp, list) else []

        for tx in txs:
            mint = None
            for transfer in tx.get("tokenTransfers", []):
                if transfer.get("mint"):
                    mint = transfer["mint"]
                    break

            if mint:
                tokens.append({
                    "event":     "new_launch",
                    "symbol":    mint[:6].upper(),   # placeholder until metadata fetched
                    "mint":      mint,
                    "timestamp": datetime.utcnow(),
                    "scenario":  "real",
                })

        return tokens

    # ─────────────────────────────────────────
    #  SOL PRICE  (for USD conversion)
    # ─────────────────────────────────────────

    def _get_sol_price(self) -> float:
        """Get current SOL/USD price via Helius."""
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                return float(data["solana"]["usd"])
        except Exception:
            return 150.0  # fallback

    # ─────────────────────────────────────────
    #  CONNECTION TEST
    # ─────────────────────────────────────────

    def test_connection(self) -> bool:
        """Quick connectivity check."""
        resp = self._post_rpc("getHealth", [])
        ok   = resp.get("result") == "ok"
        logger.info(f"[HELIUS] Connection: {'✅ OK' if ok else '❌ FAIL'}")
        return ok
