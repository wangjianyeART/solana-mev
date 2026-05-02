"""
02_fetch_arbitrages.py
──────────────────────
For every cursor in cursors/cursors.json, retrieve up to FETCH_PER_CURSOR
Jupiter v6 signatures via getSignaturesForAddress, fetch each transaction
with getTransaction (encoding=jsonParsed), and identify arbitrages.

Why standard RPC (instead of Helius Enhanced Transaction API):
  - getSignaturesForAddress  → fetch signatures
  - getTransaction (jsonParsed, concurrent asyncio) → 1 credit/tx
    (vs. 100 credits/tx for the Enhanced API)
  - reconstruct Helius-style fields locally from
    pre/postBalances + pre/postTokenBalances + innerInstructions

Output is written to jup_arb_data_cursor_std/ (kept separate from any
prior Enhanced-API output).
"""

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime

import aiohttp

# ── Config ────────────────────────────────────────────
# Set HELIUS_API_KEY in your environment before running.
HELIUS_API_KEY = os.environ["HELIUS_API_KEY"]
STD_RPC        = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

JUP_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

# ── Debug switches ────────────────────────────────────
DEBUG_LIMIT      = 20000    # only process the first N cursors; None = all
FETCH_PER_CURSOR = 1000     # signatures to pull per cursor
CONCURRENCY      = 50       # concurrent getTransaction calls
MAX_RETRIES      = 3
RPC_TIMEOUT      = 10.0

# ── Paths ─────────────────────────────────────────────
CURSORS_FILE  = "cursors/cursors.json"
DATA_DIR      = "jup_arb_data_cursor_std"
PROGRESS_FILE = os.path.join(DATA_DIR, "progress.json")
STATS_FILE    = os.path.join(DATA_DIR, "cursor_stats.json")
ARBS_PER_FILE = 10_000

JITO_TIP_ACCOUNTS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1uw5J3B5bA",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyflaDeseVtFe",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6dT",
}

DEX_PROGRAMS = {
    "SV2EYYJyRz2YhfXwXnhNAevDEui5Q6yrfyo13WtupPF": "SolFi v2",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CAMM",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h": "Raydium AMM v3",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB":  "Jupiter v4",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4":  "Jupiter v6",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG":  "Meteora DAMM v2",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB":  "Meteora LB",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo":  "Meteora DLMM Program",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA":  "PumpSwap",
    "goonuddtQRrWqqn5nFyczVKaie28f3kDkHWkHtURSLE": "GoonFi V2",
    "SSwpkEEcbUqx4vtoEByFjSkhKdCT862DNVb52nZg1UZ":  "Saber Stable Swap",
    "HpNfyc2Saw7RKkQd8nEL4khUcuPhQ7WwY1B2qjx8jxFq": "PancakeSwap",
}

IGNORED_PROGRAMS: set[str] = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
}

UNKNOWN_PROGRAM_STATS: dict[str, int] = defaultdict(int)

WSOL          = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN22_PROG  = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


# ── async RPC ─────────────────────────────────────────
async def fetch_tx_std(session: aiohttp.ClientSession, sig: str) -> dict | None:
    """Fetch a single getTransaction with retries. Returns the raw result or None."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [sig, {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        }],
    }
    for attempt in range(MAX_RETRIES):
        try:
            async with session.post(
                STD_RPC, json=payload,
                timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
            ) as r:
                data = await r.json(content_type=None)

            if "error" in data:
                code = data["error"].get("code", 0)
                if code in (-32007, -32009, -32004):
                    return None   # slot unavailable, skip
                if r.status == 429 or code == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                await asyncio.sleep(0.5)
                continue

            result = data.get("result")
            if result is None:
                return None
            if (result.get("meta") or {}).get("err") is not None:
                return None   # on-chain failure
            return result

        except (asyncio.TimeoutError, aiohttp.ClientError):
            await asyncio.sleep(0.5 * (attempt + 1))
        except Exception:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def fetch_batch_concurrent(
    sigs: list[str],
    sem: asyncio.Semaphore,
    session: aiohttp.ClientSession,
) -> list[dict | None]:
    """Fetch a batch of signatures concurrently, bounded by sem."""
    async def bounded(sig):
        async with sem:
            return await fetch_tx_std(session, sig)
    return list(await asyncio.gather(*[bounded(s) for s in sigs]))


# ── signature pull (one async call per cursor) ───────
async def fetch_signatures(
    session: aiohttp.ClientSession, program_id: str, limit: int, before: str
) -> tuple[list[str], int, int]:
    """Pull up to `limit` signatures backwards from `before`.
    Returns (success_sigs, total, fail_count)."""
    params = {"limit": min(limit, 1000), "before": before}
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getSignaturesForAddress",
        "params": [program_id, params],
    }
    for attempt in range(3):
        try:
            async with session.post(
                STD_RPC, json=payload,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                data = await r.json(content_type=None)
            page = data.get("result", [])
            total = len(page)
            ok = [r["signature"] for r in page if r.get("err") is None]
            return ok, total, total - len(ok)
        except Exception as e:
            print(f"  [!] fetch_signatures attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(3)
    return [], 0, 0


# ── standard-RPC data normalization ──────────────────
def _key_str(k) -> str:
    """Handle both jsonParsed object and string forms of accountKeys entries."""
    if isinstance(k, str):
        return k
    return k.get("pubkey", k.get("key", ""))


def build_account_keys(raw: dict) -> list[str]:
    """
    Build the full account list (legacy + v0 loaded addresses).
    Order: message.accountKeys → meta.loadedAddresses.writable → readonly.
    Indexes line up one-to-one with preBalances/postBalances.
    """
    msg  = raw.get("transaction", {}).get("message", {})
    keys = [_key_str(k) for k in msg.get("accountKeys", [])]
    loaded = (raw.get("meta") or {}).get("loadedAddresses") or {}
    keys += loaded.get("writable", [])
    keys += loaded.get("readonly", [])
    return keys


def derive_native_map(
    account_keys: list[str],
    pre_bals: list[int],
    post_bals: list[int],
) -> dict[str, int]:
    """Per-account net SOL change in lamports (Helius accountData.nativeBalanceChange)."""
    result = {}
    for i, key in enumerate(account_keys):
        pre  = pre_bals[i]  if i < len(pre_bals)  else 0
        post = post_bals[i] if i < len(post_bals) else 0
        if pre != post:
            result[key] = post - pre
    return result


def derive_token_deltas(
    account_keys: list[str],
    pre_tok: list[dict],
    post_tok: list[dict],
) -> list[dict]:
    """
    Compute per token-account balance change (Helius accountData.tokenBalanceChanges
    + tokenTransfers). Returns one dict per account:
    {account, owner, mint, pre_raw, post_raw, delta_raw, decimals}.
    """
    pre_by_idx  = {e["accountIndex"]: e for e in pre_tok}
    post_by_idx = {e["accountIndex"]: e for e in post_tok}
    all_idxs    = sorted(set(pre_by_idx) | set(post_by_idx))

    result = []
    for idx in all_idxs:
        pre  = pre_by_idx.get(idx)
        post = post_by_idx.get(idx)
        ref  = post or pre
        mint     = ref["mint"]
        decimals = int(ref["uiTokenAmount"]["decimals"])
        pre_raw  = int(pre["uiTokenAmount"]["amount"])  if pre  else 0
        post_raw = int(post["uiTokenAmount"]["amount"]) if post else 0
        delta    = post_raw - pre_raw
        owner    = ref.get("owner", "")
        account  = account_keys[idx] if idx < len(account_keys) else ""
        result.append({
            "account":   account,
            "owner":     owner,
            "mint":      mint,
            "pre_raw":   pre_raw,
            "post_raw":  post_raw,
            "delta_raw": delta,
            "decimals":  decimals,
        })
    return result


def build_tok_account_info(
    account_keys: list[str],
    pre_tok: list[dict],
    post_tok: list[dict],
) -> dict[str, dict]:
    """
    Build a token-account → (mint, owner, decimals) lookup table used to
    resolve source/dest of inner-instruction transfers.
    """
    info: dict[str, dict] = {}
    for entry in pre_tok + post_tok:
        idx = entry.get("accountIndex", -1)
        if idx < 0 or idx >= len(account_keys):
            continue
        acct = account_keys[idx]
        if acct in info:
            continue
        info[acct] = {
            "mint":     entry["mint"],
            "owner":    entry.get("owner", ""),
            "decimals": int(entry["uiTokenAmount"]["decimals"]),
        }
    return info


def extract_token_transfers(
    meta: dict,
    account_keys: list[str],
    tok_account_info: dict[str, dict],
) -> list[dict]:
    """
    Parse SPL Token transfer / transferChecked instructions from
    meta.innerInstructions, and reconstruct each actual token transfer:
    {from_owner, to_owner, mint, amount, from_acct, to_acct}.

    In jsonParsed format an inner instruction looks like:
      {
        "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "parsed": {
          "type": "transfer" | "transferChecked",
          "info": {
            "source": "<pubkey>",
            "destination": "<pubkey>",
            "authority": "<pubkey>",                    # for transfer
            "amount": "12345",                           # for transfer (string)
            "tokenAmount": {"amount": "12345", ...},     # for transferChecked
            "mint": "<mint>",                            # only for transferChecked
          }
        }
      }
    """
    transfers = []
    for group in meta.get("innerInstructions", []):
        for ix in group.get("instructions", []):
            pid = ix.get("programId", "")
            if pid not in (TOKEN_PROGRAM, TOKEN22_PROG):
                continue
            parsed = ix.get("parsed")
            if not isinstance(parsed, dict):
                continue
            typ  = parsed.get("type", "")
            info = parsed.get("info", {})
            if typ not in ("transfer", "transferChecked"):
                continue

            src  = info.get("source", "")
            dst  = info.get("destination", "")
            if not src or not dst:
                continue

            # amount
            if typ == "transferChecked":
                raw_amt = int(info.get("tokenAmount", {}).get("amount", 0))
                mint    = info.get("mint", "")
            else:
                raw_amt = int(info.get("amount", 0))
                mint    = ""

            # look up mint and owner (transferChecked may have provided mint already)
            src_info = tok_account_info.get(src, {})
            dst_info = tok_account_info.get(dst, {})
            if not mint:
                mint = src_info.get("mint") or dst_info.get("mint") or ""
            from_owner = src_info.get("owner", src)   # fallback to account pubkey when owner unknown
            to_owner   = dst_info.get("owner", dst)

            if not mint or raw_amt == 0:
                continue

            transfers.append({
                "from_acct":  src,
                "to_acct":    dst,
                "from_owner": from_owner,
                "to_owner":   to_owner,
                "mint":       mint,
                "amount":     raw_amt,
            })
    return transfers


def resolve_program_id(ix: dict, account_keys: list[str]) -> str:
    """Handle both jsonParsed (programId field) and raw (programIdIndex) formats."""
    if "programId" in ix:
        return ix["programId"]
    idx = ix.get("programIdIndex", 0)
    return account_keys[idx] if idx < len(account_keys) else ""


def normalize_tx(raw: dict) -> dict | None:
    """
    Normalize a Standard-RPC getTransaction response into a uniform dict
    used by downstream analysis. Returns None on failure / parse error.
    """
    if raw is None:
        return None
    meta = raw.get("meta") or {}
    if meta.get("err") is not None:
        return None

    tx_body      = raw.get("transaction", {})
    msg          = tx_body.get("message", {})
    account_keys = build_account_keys(raw)
    if not account_keys:
        return None

    pre_bals  = meta.get("preBalances", [])
    post_bals = meta.get("postBalances", [])
    pre_tok   = meta.get("preTokenBalances", [])
    post_tok  = meta.get("postTokenBalances", [])

    native_map      = derive_native_map(account_keys, pre_bals, post_bals)
    tok_deltas      = derive_token_deltas(account_keys, pre_tok, post_tok)
    tok_account_info = build_tok_account_info(account_keys, pre_tok, post_tok)
    token_transfers  = extract_token_transfers(meta, account_keys, tok_account_info)

    # top-level instructions: resolve programId
    top_ixs = []
    for ix in msg.get("instructions", []):
        pid = resolve_program_id(ix, account_keys)
        top_ixs.append({"programId": pid, "inner": []})

    # inner instructions: from meta.innerInstructions, keep stackHeight
    inner_by_top: dict[int, list[dict]] = defaultdict(list)
    for group in meta.get("innerInstructions", []):
        top_idx = group["index"]
        for inner_ix in group.get("instructions", []):
            pid = resolve_program_id(inner_ix, account_keys)
            sh  = inner_ix.get("stackHeight", 2)
            inner_by_top[top_idx].append({"pid": pid, "sh": sh})
    for i, top_ix in enumerate(top_ixs):
        top_ix["inner"] = inner_by_top.get(i, [])

    sigs   = tx_body.get("signatures", [])
    sig    = sigs[0] if sigs else ""
    wallet = account_keys[0] if account_keys else ""

    return {
        "signature":       sig,
        "slot":            raw.get("slot", 0),
        "block_time":      raw.get("blockTime", 0),
        "wallet":          wallet,
        "fee":             meta.get("fee", 0),
        "native_map":      native_map,
        "tok_deltas":      tok_deltas,
        "token_transfers": token_transfers,
        "instructions":    top_ixs,
    }


# ── arbitrage analysis ───────────────────────────────
def get_jito_tip(native_map: dict[str, int]) -> int:
    """Sum of SOL credited (lamports) to known Jito tip accounts."""
    return sum(
        delta for acc, delta in native_map.items()
        if acc in JITO_TIP_ACCOUNTS and delta > 0
    )


def get_used_dexes(norm: dict) -> list[str]:
    """Identify DEX venues from top-level + inner-instruction programIds."""
    used = {}
    for ix in norm["instructions"]:
        pid = ix["programId"]
        if pid in DEX_PROGRAMS:
            used[pid] = DEX_PROGRAMS[pid]
        elif pid not in IGNORED_PROGRAMS and pid:
            UNKNOWN_PROGRAM_STATS[pid] += 1
        for inner in ix["inner"]:
            inner_pid = inner["pid"]
            if inner_pid in DEX_PROGRAMS:
                used[inner_pid] = DEX_PROGRAMS[inner_pid]
            elif inner_pid not in IGNORED_PROGRAMS and inner_pid:
                UNKNOWN_PROGRAM_STATS[inner_pid] += 1
    return list(used.values())


NON_DEX_PROGRAMS: set[str] = {
    # Aggregators / routers
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    # System / runtime
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    # SPL Token
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    # Memo
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
}


def get_swap_count(instructions: list[dict]) -> int:
    """Count swap hops: only stackHeight==2 (programs invoked directly by Jupiter),
    excluding infrastructure programs, and collapse consecutive identical programs
    into a single hop. stackHeight>=3 are DEX-internal sub-calls (helper / vault),
    not standalone swaps."""
    count = 0
    for ix in instructions:
        prev_dex = None
        for inner in ix["inner"]:
            if inner["sh"] != 2:
                continue
            pid = inner["pid"]
            if pid in NON_DEX_PROGRAMS:
                continue
            if pid != prev_dex:
                count += 1
            prev_dex = pid
    return count


def get_circular_mints(
    tok_deltas: list[dict],
    native_map: dict[str, int],
    wallet: str,
) -> list[str]:
    """
    Find mints the wallet both sends and receives (cycle signature).
    Equivalent to Helius tokenTransfers sent ∩ received.
    """
    sent, received = set(), set()
    for d in tok_deltas:
        if d["owner"] == wallet:
            if d["delta_raw"] < 0:
                sent.add(d["mint"])
            elif d["delta_raw"] > 0:
                received.add(d["mint"])
    circular = list(sent & received)

    # Native SOL / wSOL cycle detection:
    # Standard RPC only exposes net deltas, so we can't see individual
    # transfer pairs the way Helius does. We use two surrogate signals:
    #   1. wSOL token delta > 0: the wallet's wSOL ATA gained (output > input → profit)
    #   2. native SOL delta > 0 (after fee): pure native-SOL arbitrage profit
    # The semantics match analyze()'s final profitability check, so plain
    # buys aren't false-flagged.
    wallet_wsol_delta = sum(
        d["delta_raw"] for d in tok_deltas
        if d["owner"] == wallet and d["mint"] == WSOL
    )
    wallet_native_delta = native_map.get(wallet, 0)
    if wallet_wsol_delta > 0 or wallet_native_delta > 0:
        circular.append("native_SOL")
    return circular


def get_arb_base_token(tok_deltas: list[dict], wallet: str) -> str | None:
    """The first mint the wallet sends, in accountIndex order."""
    for d in tok_deltas:
        if d["owner"] == wallet and d["delta_raw"] < 0:
            return d["mint"]
    return None


def get_arb_token_mints(token_raw: dict, wsol_raw: int) -> list[str]:
    result = []
    if wsol_raw > 0:
        result.append(WSOL)
    for mint, raw in token_raw.items():
        if raw > 0:
            result.append(mint)
    return result


def calc_profit(
    tok_deltas: list[dict],
    native_map: dict[str, int],
    wallet: str,
) -> dict:
    """
    Wallet's SOL / token net deltas.
    native_lamports = wallet's native SOL delta (already includes fee deduction)
    sol_lamports    = wallet's wSOL token delta
    token_raw       = deltas for all other tokens
    """
    native_lamports = native_map.get(wallet, 0)
    token_raw       = defaultdict(int)
    for d in tok_deltas:
        if d["owner"] == wallet:
            token_raw[d["mint"]] += d["delta_raw"]
    wsol_raw   = token_raw.pop(WSOL, 0)
    token_dict = dict(token_raw)
    return {
        "sol_lamports":    wsol_raw,
        "native_lamports": native_lamports,
        "token_raw":       token_dict,
        "arb_token_mints": get_arb_token_mints(token_dict, wsol_raw),
    }


def get_arb_io(
    tok_deltas: list[dict],
    wallet: str,
    arb_token_mints: list[str],
    token_transfers: list[dict] | None = None,
) -> dict:
    """
    For each arb mint, the input/output volumes (actual outflow / inflow
    from the wallet's perspective). Prefer token_transfers (real
    inner-instruction transfer amounts):
      input_raw  = total amount sent by wallet as from_owner
      output_raw = total amount received by wallet as to_owner
    decimals comes from tok_deltas.
    """
    # Build decimals map from tok_deltas
    decimals_map: dict[str, int] = {}
    for d in tok_deltas:
        if d["mint"] not in decimals_map:
            decimals_map[d["mint"]] = d["decimals"]

    result = {}
    for mint in arb_token_mints:
        if token_transfers is not None:
            input_raw  = sum(t["amount"] for t in token_transfers if t["from_owner"] == wallet and t["mint"] == mint)
            output_raw = sum(t["amount"] for t in token_transfers if t["to_owner"]   == wallet and t["mint"] == mint)
        else:
            input_raw = output_raw = 0
            for d in tok_deltas:
                if d["mint"] != mint or d["owner"] != wallet:
                    continue
                if d["delta_raw"] < 0:
                    input_raw  += abs(d["delta_raw"])
                elif d["delta_raw"] > 0:
                    output_raw += d["delta_raw"]
        result[mint] = {
            "input_raw":  input_raw,
            "output_raw": output_raw,
            "decimals":   decimals_map.get(mint, 0),
        }
    return result


def analyze(raw: dict, cursor_ts: int, cursor_ts_str: str) -> dict | None:
    """Parse a single transaction; return an arbitrage dict or None."""
    norm = normalize_tx(raw)
    if norm is None:
        return None

    wallet          = norm["wallet"]
    native_map      = norm["native_map"]
    tok_deltas      = norm["tok_deltas"]
    token_transfers = norm["token_transfers"]

    tip   = get_jito_tip(native_map)
    dexes = get_used_dexes(norm)

    if len(dexes) < 2:
        return None

    profit = calc_profit(tok_deltas, native_map, wallet)

    # ── jsonParsed transfer path: reconstruct what wallet actually sent / received
    # From inner-instruction transfers, find records where wallet is from_owner or to_owner.
    transfer_sent     = {t["mint"] for t in token_transfers if t["from_owner"] == wallet}
    transfer_received = {t["mint"] for t in token_transfers if t["to_owner"]   == wallet}

    wsol_net = profit["sol_lamports"]

    # Cycle-route tokens = both sent and received (regardless of net delta;
    # includes intermediate mints that just pass through). wSOL is judged
    # separately below.
    circular_tok = (transfer_sent & transfer_received) - {WSOL}

    # Cycle tokens used for profitability check: net delta > 0
    circular_tok_profit = {m for m in circular_tok if profit["token_raw"].get(m, 0) > 0}

    # Reject if any non-cycle token has a large negative delta (one-leg sell);
    # tolerate ≤500 raw rounding error.
    non_circular_negs = [
        v for m, v in profit["token_raw"].items()
        if m not in circular_tok and v < -500
    ]
    if non_circular_negs:
        return None

    # wSOL cycle: wSOL net up > 0, and no non-wSOL token went negative
    # (allow ±1 raw dust).
    wsol_profit = (
        wsol_net > 0
        and WSOL in transfer_sent
        and not any(v < -1 for v in profit["token_raw"].values())
    )

    # token cycle: at least one cycle token shows net profit.
    token_profit = bool(circular_tok_profit)

    # In a token-cycle arbitrage wSOL only acts as a routing leg, so its
    # net change should be near zero. A large net wSOL drop (sold wSOL for
    # token) or surge (sold token for wSOL) signals a misclassified one-way
    # swap and must be rejected. Threshold 1000 lamports = 0.000001 SOL.
    if token_profit and not wsol_profit and abs(wsol_net) > 1_000:
        return None

    if not (wsol_profit or token_profit):
        return None

    circular = list(circular_tok)
    if (wsol_profit or WSOL in transfer_sent) and WSOL not in circular:
        circular.append(WSOL)

    base      = get_arb_base_token(tok_deltas, wallet)
    arb_mints = [base] if base else profit["arb_token_mints"]
    arb_io    = get_arb_io(tok_deltas, wallet, arb_mints, token_transfers)

    return {
        "signature":         norm["signature"],
        "time":              datetime.fromtimestamp(norm["block_time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "slot":              norm["slot"],
        "wallet":            wallet,
        "dexes":             dexes,
        "swap_count":        get_swap_count(norm["instructions"]),
        "circular_mints":    circular,
        "tx_fee_lamports":   norm["fee"],
        "jito_tip_lamports": tip,
        "sol_lamports":      profit["sol_lamports"],
        "native_lamports":   profit["native_lamports"],
        "arb_token_mints":   arb_mints,
        "arb_io":            arb_io,
        "token_raw":         profit["token_raw"],
        "cursor_ts":         cursor_ts,
        "cursor_ts_str":     cursor_ts_str,
    }


# ── pretty-print ─────────────────────────────────────
def print_arb(arb: dict, idx: int):
    fee    = arb["tx_fee_lamports"]
    tip    = arb["jito_tip_lamports"]
    wsol   = arb["sol_lamports"]
    native = arb["native_lamports"]
    net    = wsol + native
    print(f"\n{'─'*60}")
    print(f"#{idx}  {arb['time']}  slot={arb['slot']}")
    print(f"  cursor   : {arb['cursor_ts_str']}")
    print(f"  signature: {arb['signature'][:60]}...")
    print(f"  searcher : {arb['wallet']}")
    print(f"  DEX      : {' + '.join(arb['dexes'])}")
    print(f"  hops     : {arb['swap_count']}")
    print(f"  arb token (input → output):")
    for m, io in arb["arb_io"].items():
        label = "wSOL" if m == WSOL else m[:20] + "..."
        dec   = io["decimals"]
        inp   = io["input_raw"]
        out   = io["output_raw"]
        gain  = out - inp
        print(f"    {label}")
        print(f"      input : {inp} raw")
        print(f"      output: {out} raw")
        print(f"      diff  : {'+' if gain>=0 else ''}{gain} raw")
    print(f"  on-chain fee   : -{fee} lamports")
    print(f"  Jito tip       : -{tip} lamports")
    print(f"  wSOL delta     : {'+' if wsol>=0 else ''}{wsol} lamports")
    print(f"  native SOL Δ   : {'+' if native>=0 else ''}{native} lamports")
    print(f"  ── net profit  : {'+' if net>=0 else ''}{net} lamports")
    if arb["token_raw"]:
        for mint, raw in arb["token_raw"].items():
            print(f"  Token({mint[:8]}...): {'+' if raw>0 else ''}{raw} (raw)")


# ── checkpoint utilities ─────────────────────────────
def batch_path(batch_num: int) -> str:
    return os.path.join(DATA_DIR, f"arbs_{batch_num:04d}.json")


def load_progress() -> dict:
    defaults = {"done_ts": [], "batch_num": 1, "arbs_in_batch": 0, "total_arbs": 0}
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            saved = json.load(f)
        defaults.update(saved)
    defaults["done_ts"] = set(defaults["done_ts"])
    return defaults


def save_progress(prog: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    out = dict(prog)
    out["done_ts"] = sorted(out["done_ts"])
    with open(PROGRESS_FILE, "w") as f:
        json.dump(out, f, indent=2)


def append_cursor_stat(stat: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    stats = []
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, encoding="utf-8") as f:
            stats = json.load(f)
    stats.append(stat)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def append_arbs(arbs: list[dict], prog: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    pending = list(arbs)
    while pending:
        path    = batch_path(prog["batch_num"])
        current = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
        slots   = ARBS_PER_FILE - len(current)
        chunk   = pending[:slots]
        pending = pending[slots:]
        current.extend(chunk)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
        prog["arbs_in_batch"] = len(current)
        if len(current) >= ARBS_PER_FILE:
            print(f"    [{path}] reached {ARBS_PER_FILE} records, rolling over to next file")
            prog["batch_num"]     += 1
            prog["arbs_in_batch"]  = 0


# ── main async loop ──────────────────────────────────
async def process_cursor(
    cursor: dict,
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    cursor_ts: int,
    cursor_ts_str: str,
) -> tuple[list[dict], dict]:
    """Concurrently fetch all transactions for one cursor, classify
    arbitrages, return (arbs, stat)."""
    sig = cursor["sig"]

    # pull signatures (returns precise total and failure count)
    sigs, cnt_total, cnt_err_tx = await fetch_signatures(session, JUP_V6, FETCH_PER_CURSOR, sig)
    cnt_fetched = len(sigs)

    if not sigs:
        stat = {
            "cursor_ts": cursor_ts, "cursor_ts_str": cursor_ts_str,
            "cnt_total_sig": cnt_total,
            "cnt_fail_sig": cnt_err_tx,
            "cnt_success_sig": 0,
            "fail_sig_rate_pct": 100.0 if cnt_total else 0.0,
            "cnt_parsed": 0, "cnt_parse_fail": 0,
            "cnt_arb": 0, "cnt_non_arb": 0,
            "arb_rate_pct": 0.0, "non_arb_rate_pct": 0.0,
        }
        return [], stat

    # concurrent transaction fetch
    raw_txs = await fetch_batch_concurrent(sigs, sem, session)

    cnt_parsed     = sum(1 for r in raw_txs if r is not None)
    cnt_parse_fail = cnt_fetched - cnt_parsed
    cnt_arb = cnt_non_arb = 0
    arbs = []

    for raw in raw_txs:
        if raw is None:
            continue
        arb = analyze(raw, cursor_ts, cursor_ts_str)
        if arb:
            cnt_arb += 1
            arbs.append(arb)
        else:
            cnt_non_arb += 1

    fail_sig_rate = cnt_err_tx / cnt_total * 100 if cnt_total else 0
    arb_rate      = cnt_arb     / cnt_parsed * 100 if cnt_parsed else 0
    non_arb_rate  = cnt_non_arb / cnt_parsed * 100 if cnt_parsed else 0

    stat = {
        "cursor_ts":         cursor_ts,
        "cursor_ts_str":     cursor_ts_str,
        "cnt_total_sig":     cnt_total,
        "cnt_fail_sig":      cnt_err_tx,
        "cnt_success_sig":   cnt_fetched,
        "fail_sig_rate_pct": round(fail_sig_rate, 2),
        "cnt_parsed":        cnt_parsed,
        "cnt_parse_fail":    cnt_parse_fail,
        "cnt_arb":           cnt_arb,
        "cnt_non_arb":       cnt_non_arb,
        "arb_rate_pct":      round(arb_rate, 2),
        "non_arb_rate_pct":  round(non_arb_rate, 2),
    }

    print(f"  total sigs: {cnt_total}  failed: {cnt_err_tx} ({fail_sig_rate:.1f}%)  ok: {cnt_fetched}")
    print(f"  parsed ok : {cnt_parsed}  parse fail: {cnt_parse_fail}")
    print(f"  arb       : {cnt_arb} ({arb_rate:.1f}%)  non-arb: {cnt_non_arb} ({non_arb_rate:.1f}%)")

    return arbs, stat


async def amain():
    print("=" * 60)
    print("02_fetch_arbitrages.py  —  Standard RPC + asyncio")
    print(f"  CONCURRENCY={CONCURRENCY}  FETCH_PER_CURSOR={FETCH_PER_CURSOR}")
    if DEBUG_LIMIT:
        print(f"  *** debug mode: only the first {DEBUG_LIMIT} cursors ***")
    print("=" * 60)

    with open(CURSORS_FILE, encoding="utf-8") as f:
        cursors = json.load(f)
    cursors = [c for c in cursors if c.get("sig")]
    print(f"\n{len(cursors)} valid cursors")

    if DEBUG_LIMIT:
        cursors = sorted(cursors, key=lambda x: x["ts"], reverse=True)
        seen_sigs, unique = set(), []
        for c in cursors:
            if c["sig"] not in seen_sigs:
                seen_sigs.add(c["sig"])
                unique.append(c)
            if len(unique) >= DEBUG_LIMIT:
                break
        cursors = sorted(unique, key=lambda x: x["ts"])
        print(f"debug mode: keeping the most recent {len(cursors)} unique-sig cursors")

    prog    = load_progress()
    pending = [c for c in cursors if c["ts"] not in prog["done_ts"]]
    print(f"done: {len(prog['done_ts'])}  pending: {len(pending)}\n")

    if not pending:
        print("All done.")
        return

    conn = aiohttp.TCPConnector(limit=CONCURRENCY + 5)
    async with aiohttp.ClientSession(connector=conn) as session:
        sem = asyncio.Semaphore(CONCURRENCY)

        total_new_arbs = 0
        for i, cursor in enumerate(pending):
            ts     = cursor["ts"]
            ts_str = cursor["ts_str"]
            print(f"[{i+1}/{len(pending)}] cursor={ts_str}  sig={cursor['sig'][:20]}...")

            arbs, stat = await process_cursor(cursor, session, sem, ts, ts_str)

            for k, arb in enumerate(arbs, 1):
                print_arb(arb, k)

            append_arbs(arbs, prog)
            append_cursor_stat(stat)
            prog["total_arbs"] += len(arbs)
            prog["done_ts"].add(ts)
            total_new_arbs += len(arbs)
            save_progress(prog)

            print(f"  cumulative arbs: {prog['total_arbs']}  cursors done: {len(prog['done_ts'])}")

    print(f"\n{'='*60}")
    print(f"new arbs this run: {total_new_arbs}")
    print(f"total arbs       : {prog['total_arbs']}")
    print(f"data directory   : {DATA_DIR}/")

    if UNKNOWN_PROGRAM_STATS:
        print(f"\nUnknown programIds (occurrence count):")
        for pid, cnt in sorted(UNKNOWN_PROGRAM_STATS.items(), key=lambda x: -x[1])[:10]:
            print(f"  {pid}  count={cnt}")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
