# APEX — Helius Client (Free Tier Compatible)
# Uses standard JSON-RPC only — no paid REST endpoints

import json
import logging
import urllib.request
from datetime import datetime
from typing import Optional
import random

logger = logging.getLogger("apex.helius")


class HeliusClient:

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

    # ─────────────────────────────────────────
    #  JSON-RPC  (Free tier supported)
    # ─────────────────────────────────────────

    def _rpc(self, method: str, params: list) -> dict:
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

    # ─────────────────────────────────────────
    #  VOLUME DATA  (Free tier via RPC)
    # ─────────────────────────────────────────

    async def get_volume_data(self, mint_address: str, scenario: str = "") -> dict:
        """
        Free tier: use getTokenLargestAccounts + getTokenSupply
        to approximate concentration and activity level.
        """
        # Get token supply
        supply_resp = self._rpc("getTokenSupply", [mint_address])
        supply_val  = supply_resp.get("result", {}).get("value", {})
        supply      = float(supply_val.get("uiAmount", 0) or 0)

        # Get largest accounts (whale concentration proxy)
        holders_resp = self._rpc("getTokenLargestAccounts", [mint_address])
        holders      = holders_resp.get("result", {}).get("value", [])

        if not holders or supply == 0:
            logger.warning(f"[HELIUS] No data for {mint_address[:12]}...")
            return self._empty_result(mint_address)

        # Calculate concentration sigma
        # High concentration in top holder = unusual activity
        top_holder_pct = float(holders[0].get("uiAmount", 0)) / supply if supply > 0 else 0
        top5_pct       = sum(float(h.get("uiAmount", 0)) for h in holders[:5]) / supply if supply > 0 else 0

        # Sigma approximation:
        # Normal distribution: top holder ~5%, top5 ~25%
        # Anomaly: top holder >20%, top5 >60%
        concentration_score = (top_holder_pct / 0.05) if top_holder_pct > 0 else 0
        sigma = min(concentration_score * 2, 15.0)

        # Netflow approximation from holder count
        holder_count = len(holders)
        netflow_est  = holder_count * 1000  # rough proxy

        logger.info(
            f"[HELIUS] {mint_address[:8]}... | "
            f"top_holder={top_holder_pct:.1%} top5={top5_pct:.1%} "
            f"σ≈{sigma:.1f} holders={holder_count}"
        )

        return {
            "mint":           mint_address,
            "volume_sigma":   round(sigma, 2),
            "netflow_usd":    round(netflow_est, 0),
            "buy_count":      holder_count,
            "sell_count":     0,
            "unique_wallets": holder_count,
        }

    # ─────────────────────────────────────────
    #  HONEYPOT CHECK  (Free tier)
    # ─────────────────────────────────────────

    async def check_honeypot(self, mint_address: str) -> bool:
        resp = self._rpc("getAccountInfo", [
            mint_address,
            {"encoding": "jsonParsed"}
        ])
        result = resp.get("result", {})
        if not result or not result.get("value"):
            return True  # Unknown = risky

        data   = result.get("value", {}).get("data", {})
        parsed = data.get("parsed", {}) if isinstance(data, dict) else {}
        info   = parsed.get("info", {})

        # Freeze authority = honeypot risk
        if info.get("freezeAuthority"):
            logger.warning(f"[HELIUS] {mint_address[:8]} has freeze authority")
            return True

        return False

    # ─────────────────────────────────────────
    #  DEV WALLET  (Free tier)
    # ─────────────────────────────────────────

    async def get_dev_wallet_pct(self, mint_address: str) -> float:
        supply_resp = self._rpc("getTokenSupply", [mint_address])
        supply      = float(supply_resp.get("result", {})
                            .get("value", {}).get("uiAmount", 1) or 1)

        holders_resp = self._rpc("getTokenLargestAccounts", [mint_address])
        holders      = holders_resp.get("result", {}).get("value", [])

        if not holders:
            return 0.0

        largest = float(holders[0].get("uiAmount", 0))
        return round(largest / supply, 4) if supply > 0 else 0.0

    # ─────────────────────────────────────────
    #  NEW PUMP TOKENS  (Free tier via signatures)
    # ─────────────────────────────────────────

    async def get_new_pump_tokens(self, limit: int = 5) -> list[dict]:
        PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

        # Get recent signatures for Pump.fun program
        resp = self._rpc("getSignaturesForAddress", [
            PUMP_FUN,
            {"limit": limit}
        ])
        sigs = resp.get("result", [])

        tokens = []
        for sig in sigs[:3]:   # Limit to avoid rate limiting
            tx_resp = self._rpc("getTransaction", [
                sig.get("signature", ""),
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ])
            tx = tx_resp.get("result")
            if not tx:
                continue

            # Extract mint from post token balances
            meta = tx.get("meta", {})
            for bal in meta.get("postTokenBalances", []):
                mint = bal.get("mint", "")
                if mint and len(mint) >= 32:
                    tokens.append({
                        "event":     "new_launch",
                        "symbol":    mint[:6].upper(),
                        "mint":      mint,
                        "timestamp": datetime.utcnow(),
                    })
                    break

        return tokens

    # ─────────────────────────────────────────
    #  CONNECTION TEST
    # ─────────────────────────────────────────

    def test_connection(self) -> bool:
        resp = self._rpc("getHealth", [])
        ok   = resp.get("result") == "ok"
        logger.info(f"[HELIUS] Connection: {'✅ OK' if ok else '❌ FAIL'}")
        return ok

    def _get_sol_price(self) -> float:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            with urllib.request.urlopen(url, timeout=5) as resp:
                return float(json.loads(resp.read())["solana"]["usd"])
        except Exception:
            return 150.0

    def _empty_result(self, mint: str) -> dict:
        return {
            "mint":           mint,
            "volume_sigma":   0.0,
            "netflow_usd":    0.0,
            "buy_count":      0,
            "sell_count":     0,
            "unique_wallets": 0,
        }
