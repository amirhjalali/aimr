#!/usr/bin/env python3
"""Cross-account token/quota ledger for AIMR (AI Model Routing).

Append-only JSONL ledger + per-pool caps. Three subcommands:

  budget.py log --pool codex --calls 1 [--tokens N] [--dollars X]
                [--event image-gen] [--confidence estimated|exact]
  budget.py status                    # human-readable cross-pool view
  budget.py remaining --pool codex    # machine-readable; exit 3 = below reserve

Pools are accounts, not vendors: two Claude subscriptions are two pools.
Every ledger line carries a confidence field so displays never pretend
estimates are exact. Unconfigured pools are treated as unlimited (logged,
never blocking) — configure caps in budget.json (see budget.example.json).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "ledger.jsonl"
CONFIG = HERE / "budget.json"

EXIT_OK = 0
EXIT_BELOW_RESERVE = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {"pools": {}}


def window_start(period: str, now: datetime) -> datetime:
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        start = midnight
    elif period == "week":
        start = midnight - timedelta(days=local.weekday())
    elif period == "month":
        start = midnight.replace(day=1)
    else:
        raise ValueError(f"unknown period: {period}")
    return start.astimezone(timezone.utc)


def read_ledger(since: datetime | None = None) -> list[dict]:
    if not LEDGER.exists():
        return []
    entries = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # never let one corrupt line kill accounting
        if since is not None:
            try:
                ts = datetime.fromisoformat(entry["ts"])
            except (KeyError, ValueError):
                continue
            if ts < since:
                continue
        entries.append(entry)
    return entries


def append_entry(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def pool_spend(pool: str, unit: str, since: datetime) -> tuple[float, int, int]:
    """Return (total in unit, exact-line count, estimated-line count)."""
    total, exact, estimated = 0.0, 0, 0
    for e in read_ledger(since):
        if e.get("pool") != pool:
            continue
        total += float(e.get(unit, 0) or 0)
        if e.get("confidence") == "exact":
            exact += 1
        else:
            estimated += 1
    return total, exact, estimated


def cmd_log(args: argparse.Namespace) -> int:
    entry = {
        "ts": _now().isoformat(timespec="seconds"),
        "pool": args.pool,
        "event": args.event,
        "confidence": args.confidence,
    }
    if args.tokens is not None:
        entry["tokens"] = args.tokens
    if args.calls is not None:
        entry["calls"] = args.calls
    if args.dollars is not None:
        entry["dollars"] = args.dollars
    if not any(k in entry for k in ("tokens", "calls", "dollars")):
        print("error: provide at least one of --tokens/--calls/--dollars", file=sys.stderr)
        return 2
    append_entry(entry)
    return EXIT_OK


def pool_state(name: str, cfg: dict) -> dict:
    unit = cfg.get("unit", "calls")
    period = cfg.get("period", "week")
    cap = cfg.get("cap")
    reserve = cfg.get("reserve_fraction", 0.0)
    since = window_start(period, _now())
    spent, exact, estimated = pool_spend(name, unit, since)
    state = {
        "pool": name, "unit": unit, "period": period, "cap": cap,
        "spent": spent, "exact_lines": exact, "estimated_lines": estimated,
    }
    if cap is not None:
        state["remaining"] = max(0.0, cap - spent)
        state["below_reserve"] = (cap - spent) <= cap * reserve
    else:
        state["remaining"] = None
        state["below_reserve"] = False
    return state


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    pools = dict(config.get("pools", {}))
    # Surface pools that appear in the ledger but aren't configured.
    for e in read_ledger():
        pools.setdefault(e.get("pool", "?"), {})
    if not pools:
        print("no pools configured and no ledger entries yet")
        return EXIT_OK
    for name in sorted(pools):
        s = pool_state(name, pools[name])
        conf = f"({s['exact_lines']} exact / {s['estimated_lines']} estimated lines)"
        if s["cap"] is None:
            print(f"{name:16s} spent {s['spent']:g} {s['unit']}/{s['period']}  cap: unlimited  {conf}")
        else:
            flag = "  ⚠ BELOW RESERVE" if s["below_reserve"] else ""
            print(f"{name:16s} spent {s['spent']:g}/{s['cap']:g} {s['unit']}/{s['period']}  "
                  f"remaining {s['remaining']:g}  {conf}{flag}")
    return EXIT_OK


def cmd_remaining(args: argparse.Namespace) -> int:
    config = load_config()
    cfg = config.get("pools", {}).get(args.pool)
    if cfg is None:
        print("unlimited")
        return EXIT_OK
    s = pool_state(args.pool, cfg)
    print("unlimited" if s["remaining"] is None else f"{s['remaining']:g}")
    return EXIT_BELOW_RESERVE if s["below_reserve"] else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="append a spend line")
    lg.add_argument("--pool", required=True)
    lg.add_argument("--tokens", type=float)
    lg.add_argument("--calls", type=float)
    lg.add_argument("--dollars", type=float)
    lg.add_argument("--event", default="")
    lg.add_argument("--confidence", choices=["exact", "estimated"], default="estimated")
    lg.set_defaults(fn=cmd_log)

    st = sub.add_parser("status", help="cross-pool spend view")
    st.set_defaults(fn=cmd_status)

    rm = sub.add_parser("remaining", help="remaining for one pool; exit 3 = below reserve")
    rm.add_argument("--pool", required=True)
    rm.set_defaults(fn=cmd_remaining)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
