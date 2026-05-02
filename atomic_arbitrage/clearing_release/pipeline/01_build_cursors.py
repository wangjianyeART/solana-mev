"""
01_build_cursors.py  —  interpolation + asyncio concurrent version
─────────────────────────────────────────────────────────────────
Goal: for the past year, sample 60 timestamps per UTC day (one every
      1440 s), find the Solana slot whose block-time matches each
      target timestamp, and record the first signature in that slot.

Algorithm:
  1. Bootstrap a linear (slot, ts) interpolation model from two anchors.
  2. For each target ts, estimate the slot directly via interpolation;
     usually the error is < 10 s, so 1–2 secant-method refinements hit
     the target.
  3. asyncio concurrency (default 20 coroutines), ~200–300 req/s
     effective throughput.

Output: cursors/cursors.json
  [ { "ts": 1710000000, "ts_str": "...", "slot": 123456789,
      "block_time": 1710000001, "sig": "xxx..." }, ... ]
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import aiohttp

# ── Config ────────────────────────────────────────────
# Set HELIUS_API_KEY in your environment before running, e.g.
#   export HELIUS_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
HELIUS_API_KEY = os.environ["HELIUS_API_KEY"]
HELIUS_STD_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

POINTS_PER_DAY  = 60
INTERVAL_SECS   = 86400 // POINTS_PER_DAY   # 1440 s
DAYS            = 365
TOLERANCE_SECS  = 2       # tolerance between slot blockTime and target ts (seconds)
MAX_REFINE      = 5       # max refinements after the initial interpolation
CONCURRENCY     = 20      # number of concurrent coroutines (Helius free tier: ≤25)
SAVE_INTERVAL   = 100     # auto-save every N completed cursors

OUT_DIR  = "cursors"
OUT_FILE = os.path.join(OUT_DIR, "cursors.json")


# ── async RPC ────────────────────────────────────────
async def arpc(session: aiohttp.ClientSession, method: str, params: list,
               retries: int = 6) -> dict | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(retries):
        try:
            async with session.post(HELIUS_STD_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json(content_type=None)
            if "error" in data:
                code = data["error"].get("code", 0)
                if code in (-32007, -32009, -32004):
                    return None   # slot skipped / not available
                # 429 rate limit
                if r.status == 429 or code == 429:
                    await asyncio.sleep(1 + attempt)
                    continue
                await asyncio.sleep(0.5)
                continue
            return data.get("result")
        except Exception:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def get_block_time(session, slot: int) -> int | None:
    return await arpc(session, "getBlockTime", [slot])


async def get_first_sig(session, slot: int) -> str | None:
    """Return the first signature in the given slot; if the slot is empty or
    skipped, scan up to 20 slots forward."""
    for s in range(slot, slot + 20):
        result = await arpc(session, "getBlock", [s, {
            "encoding": "base64",
            "transactionDetails": "signatures",
            "rewards": False,
            "maxSupportedTransactionVersion": 0,
        }])
        if result:
            sigs = result.get("signatures", [])
            if sigs:
                return sigs[0]
    return None


# ── interpolation model ──────────────────────────────
class SlotInterpolator:
    """Estimate slot for a target timestamp via linear interpolation between
    two anchor points. Updated online as more anchors are observed."""

    def __init__(self):
        self.anchors: list[tuple[int, int]] = []   # [(slot, ts), ...]

    def add(self, slot: int, ts: int):
        self.anchors.append((slot, ts))
        # keep only the most recent 20 anchors; use oldest and newest for slope
        if len(self.anchors) > 20:
            self.anchors = self.anchors[-20:]

    def estimate(self, target_ts: int) -> int:
        if len(self.anchors) < 2:
            # fallback: estimate from genesis
            GENESIS_TS    = 1584365340
            SLOT_DURATION = 0.4
            return max(0, int((target_ts - GENESIS_TS) / SLOT_DURATION))

        # use the oldest and newest anchors for linear extrapolation/interpolation
        (s0, t0) = self.anchors[0]
        (s1, t1) = self.anchors[-1]
        if t1 == t0:
            return s1
        slope = (s1 - s0) / (t1 - t0)   # slot/s
        est = s0 + slope * (target_ts - t0)
        return max(0, int(est))


# ── core: locate slot + sig for a single timestamp ──
async def find_cursor(session: aiohttp.ClientSession,
                      target_ts: int,
                      interp: SlotInterpolator,
                      interp_lock: asyncio.Lock,
                      current_slot: int) -> dict:
    ts_str = datetime.fromtimestamp(target_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. initial slot estimate via interpolation
    async with interp_lock:
        est = interp.estimate(target_ts)
    est = max(0, min(est, current_slot))

    # 2. fetch blockTime for est, then iteratively refine
    slot = est
    bt   = await get_block_time(session, slot)

    # if the slot does not exist, scan rightward
    if bt is None:
        for delta in range(1, 50):
            bt = await get_block_time(session, slot + delta)
            if bt is not None:
                slot += delta
                break

    if bt is None:
        return {"ts": target_ts, "ts_str": ts_str, "slot": None, "block_time": None, "sig": None}

    # 3. secant-method refinement using current interpolation slope
    for _ in range(MAX_REFINE):
        diff = target_ts - bt
        if abs(diff) <= TOLERANCE_SECS:
            break

        async with interp_lock:
            slope = _get_slope(interp, slot, bt)

        step = int(diff * slope)
        if step == 0:
            step = 1 if diff > 0 else -1

        new_slot = max(0, min(slot + step, current_slot))
        new_bt   = await get_block_time(session, new_slot)

        # skip empty slots
        if new_bt is None:
            direction = 1 if diff > 0 else -1
            for d in range(1, 30):
                new_bt = await get_block_time(session, new_slot + direction * d)
                if new_bt is not None:
                    new_slot += direction * d
                    break

        if new_bt is None:
            break

        # update interpolation model
        async with interp_lock:
            interp.add(new_slot, new_bt)

        slot = new_slot
        bt   = new_bt

    # 4. fetch signature
    sig = await get_first_sig(session, slot)

    # 5. update interpolation model
    if bt is not None:
        async with interp_lock:
            interp.add(slot, bt)

    return {
        "ts":         target_ts,
        "ts_str":     ts_str,
        "slot":       slot,
        "block_time": bt,
        "sig":        sig,
    }


def _get_slope(interp: SlotInterpolator, fallback_slot: int, fallback_ts: int) -> float:
    """Return the slope (slot/s) from the interpolator."""
    if len(interp.anchors) >= 2:
        (s0, t0) = interp.anchors[0]
        (s1, t1) = interp.anchors[-1]
        if t1 != t0:
            return (s1 - s0) / (t1 - t0)
    return 2.5   # default ~400 ms/slot


# ── resumable checkpointing ──────────────────────────
def load_cursors() -> list[dict]:
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_cursors(cursors: list[dict]):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(cursors, key=lambda x: x["ts"]), f, indent=2, ensure_ascii=False)
    os.replace(tmp, OUT_FILE)


# ── generate target timestamps ───────────────────────
def build_target_timestamps() -> list[int]:
    now         = int(datetime.now(timezone.utc).timestamp())
    today_start = (now // 86400) * 86400
    targets = []
    for day_offset in range(DAYS):
        day_start = today_start - day_offset * 86400
        for pt in range(POINTS_PER_DAY):
            targets.append(day_start + pt * INTERVAL_SECS)
    return sorted(set(targets))


# ── concurrent main loop ─────────────────────────────
async def amain():
    print("=" * 60)
    print("01_build_cursors.py  —  interpolation + concurrent")
    print("=" * 60)

    targets = build_target_timestamps()
    print(f"\nTarget timestamps: {len(targets)}")
    print(f"  {datetime.fromtimestamp(targets[0],  tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
          f"  →  {datetime.fromtimestamp(targets[-1], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

    existing = load_cursors()
    done_ts  = {c["ts"] for c in existing if c.get("sig")}   # entries with sig=null are retried
    pending  = [ts for ts in targets if ts not in done_ts]
    print(f"Done (with sig): {len(done_ts)}  Remaining: {len(pending)}")

    if not pending:
        print("All done.")
        return

    # limit concurrent connections via aiohttp connector
    conn = aiohttp.TCPConnector(limit=CONCURRENCY + 5)
    async with aiohttp.ClientSession(connector=conn) as session:

        # fetch current slot
        current_slot = await arpc(session, "getSlot", [{"commitment": "finalized"}])
        print(f"Current slot: {current_slot:,}\n")

        interp      = SlotInterpolator()
        interp_lock = asyncio.Lock()

        # control concurrency via semaphore
        sem = asyncio.Semaphore(CONCURRENCY)

        cursors: list[dict] = [c for c in existing if c.get("sig")]
        done_count  = 0
        start_time  = time.time()

        async def worker(target_ts: int):
            async with sem:
                result = await find_cursor(session, target_ts, interp, interp_lock, current_slot)
                return result

        # submit tasks in batches of SAVE_INTERVAL, save after each batch
        for batch_start in range(0, len(pending), SAVE_INTERVAL):
            batch = pending[batch_start: batch_start + SAVE_INTERVAL]
            tasks = [asyncio.create_task(worker(ts)) for ts in batch]
            results = await asyncio.gather(*tasks)

            for r in results:
                cursors.append(r)
                done_count += 1

            save_cursors(cursors)

            elapsed  = time.time() - start_time
            rate     = done_count / elapsed if elapsed > 0 else 0
            remain   = len(pending) - done_count
            eta_secs = remain / rate if rate > 0 else 0

            with_sig = sum(1 for c in results if c.get("sig"))
            print(f"  progress {done_count}/{len(pending)}  "
                  f"batch with-sig={with_sig}/{len(batch)}  "
                  f"rate={rate:.1f}/s  "
                  f"ETA={eta_secs/60:.1f}min  "
                  f"saved→{OUT_FILE}")

    final = sorted(cursors, key=lambda x: x["ts"])
    save_cursors(final)
    with_sig = sum(1 for c in final if c.get("sig"))
    print(f"\n{'='*60}")
    print(f"Done. {len(final)} cursors total, {with_sig} with sig, {len(final)-with_sig} without")
    print(f"Output: {OUT_FILE}")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
