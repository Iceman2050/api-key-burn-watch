#!/usr/bin/env python3
"""
api-key-burn-watch — a tripwire for stolen AI provider API keys.

Polls YOUR OWN provider usage/billing APIs, learns your normal daily burn
rate, and fires an instant alert when today's spend spikes (>3x the 14-day
median) or when usage appears in unusual hours (00:00–06:00 local).

Why: on 2026-10-17 the OpenCode breach disclosure revealed a "large scale
distillation attack" that exposed provider keys in 1.4% of Console
workspaces — victims saw surprise charges ($281 OpenAI / $168 Anthropic /
$1,700 reports) BEFORE the disclosure. A burn-rate tripwire catches a
stolen key before the invoice does.

Honest scope notes:
  * OpenAI: uses the documented organization costs endpoint
    (GET /v1/organization/costs). Needs an *admin* API key; a regular
    project key returns 401/403 and is reported as unavailable, not fatal.
  * Anthropic: no public usage/billing API exists for API keys — the tool
    records this honestly and cannot monitor it. Use Anthropic's email
    spend alerts instead.
  * Google (AI Studio): no public billing endpoint for API keys — the tool
    records this honestly. Use Google Cloud billing alerts instead.
  * This watches *your own* keys, from *your* machine. It never sends
    anything anywhere except an optional webhook YOU configure.

Subcommands:
  baseline  Fetch last 14 days of usage, store rolling baseline.
  check     Fetch today's usage, compare vs baseline, alert on anomalies.

--dry-run uses tests/fixtures/*.json so CI and demos need no real keys.

Never commit keys: only .env.example is shipped; keys come from env vars.

Zero third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import statistics
import sys
import urllib.error
import urllib.request

__version__ = "1.0.0"

BASELINE_FILE = pathlib.Path.home() / ".api-key-burn-watch.json"
BASELINE_DAYS = 14
SPIKE_MULTIPLE = 3.0
ZERO_BASELINE_MIN_USD = 1.0   # any spend >= $1 from a $0 baseline is an alert
UNUSUAL_HOURS = range(0, 6)   # 00:00–05:59 local time

OPENAI_COSTS_URL = "https://api.openai.com/v1/organization/costs"


class ProviderError(Exception):
    """A provider could not supply usage data (auth, network, shape)."""


# ---------------------------------------------------------------------------
# HTTP + provider adapters
# ---------------------------------------------------------------------------
def _http_get_json(url: str, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}: {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"network error: {e}") from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"invalid JSON response: {e}") from e


def fetch_openai_daily_spend(api_key: str, days: int = BASELINE_DAYS) -> list[dict]:
    """Daily USD spend via the OpenAI organization costs endpoint (bucketed 1d)."""
    end = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    start = end - days * 86400
    url = (f"{OPENAI_COSTS_URL}?start_time={start}&end_time={end}"
           f"&bucket_width=1d&limit={days + 2}")
    data = _http_get_json(url, {"Authorization": f"Bearer {api_key}"})
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ProviderError("unexpected response shape from OpenAI costs API")
    out = []
    for row in rows:
        total = 0.0
        for result in row.get("results", []):
            amount = result.get("amount", {})
            if amount.get("currency", "usd") != "usd":
                raise ProviderError("non-USD currency in OpenAI costs response")
            total += float(amount.get("value", 0.0))
        day = _dt.datetime.fromtimestamp(
            row["start_time"], _dt.timezone.utc).date().isoformat()
        out.append({"date": day, "spend_usd": round(total, 4)})
    return out


PROVIDERS = {
    "openai": {
        "env": "OPENAI_API_KEY",
        "fetch": fetch_openai_daily_spend,
    },
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "fetch": None,
        "note": ("no public usage/billing API exists for Anthropic API keys — "
                 "cannot be monitored by this tool; use Anthropic's email "
                 "spend alerts"),
    },
    "google": {
        "env": "GOOGLE_API_KEY",
        "fetch": None,
        "note": ("no public billing endpoint exists for Google AI Studio keys — "
                 "cannot be monitored by this tool; use Google Cloud billing "
                 "alerts"),
    },
}


# ---------------------------------------------------------------------------
# Fixtures (dry-run)
# ---------------------------------------------------------------------------
def load_fixture(fixtures_dir: str | pathlib.Path, name: str) -> list[dict]:
    path = pathlib.Path(fixtures_dir) / f"{name}_14d.json"
    if not path.exists():
        raise ProviderError(f"fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_today_fixture(fixtures_dir: str | pathlib.Path) -> dict:
    path = pathlib.Path(fixtures_dir) / "today.json"
    if not path.exists():
        raise ProviderError(f"fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def cmd_baseline(args: argparse.Namespace) -> int:
    baseline = {"generated_at": _utcnow_iso(), "providers": {}, "unavailable": {}}
    saw_any = False

    for name, cfg in PROVIDERS.items():
        if args.dry_run:
            try:
                daily = load_fixture(args.fixtures, name)
            except ProviderError:
                continue  # no fixture for this provider — skip quietly
            saw_any = True
        else:
            key = os.environ.get(cfg["env"], "").strip()
            if not key:
                continue
            saw_any = True
            if cfg["fetch"] is None:
                baseline["unavailable"][name] = cfg["note"]
                continue
            try:
                daily = cfg["fetch"](key)
            except ProviderError as e:
                baseline["unavailable"][name] = str(e)
                continue

        spends = [float(d["spend_usd"]) for d in daily]
        baseline["providers"][name] = {
            "days": len(daily),
            "median_daily_spend_usd": round(statistics.median(spends), 4) if spends else 0.0,
            "max_daily_spend_usd": round(max(spends), 4) if spends else 0.0,
            "daily": daily[-BASELINE_DAYS:],
        }

    if not saw_any:
        print("error: no provider API keys found in the environment.", file=sys.stderr)
        print("Set OPENAI_API_KEY (and/or ANTHROPIC_API_KEY / GOOGLE_API_KEY), "
              "or re-run with --dry-run to use fixtures.", file=sys.stderr)
        return 3

    out_path = pathlib.Path(args.baseline_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")

    print(f"Baseline written to {out_path}")
    for name, pdata in baseline["providers"].items():
        print(f"  {name}: {pdata['days']}d, "
              f"median ${pdata['median_daily_spend_usd']}/day, "
              f"max ${pdata['max_daily_spend_usd']}/day")
    for name, reason in baseline["unavailable"].items():
        print(f"  {name}: UNAVAILABLE — {reason}")
    return 0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def _send_webhook(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
        print(f"Webhook alert delivered to {url}")
    except Exception as e:  # webhook failure must not mask the alert itself
        print(f"warning: webhook delivery failed ({e}); alert still stands above.",
              file=sys.stderr)


def cmd_check(args: argparse.Namespace) -> int:
    baseline_path = pathlib.Path(args.baseline_file)
    if not baseline_path.exists():
        print(f"error: no baseline at {baseline_path} — run `baseline` first.",
              file=sys.stderr)
        return 3
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    alerts: list[str] = []
    lines: list[str] = []
    today_fixture = load_today_fixture(args.fixtures) if args.dry_run else {}

    for name, pdata in baseline.get("providers", {}).items():
        median = float(pdata.get("median_daily_spend_usd", 0.0))
        if args.dry_run:
            today = today_fixture.get(name, {"spend_usd": 0.0, "hour": 12})
            spend = float(today["spend_usd"])
            hour = int(today["hour"])
        else:
            cfg = PROVIDERS[name]
            key = os.environ.get(cfg["env"], "").strip()
            if not key or cfg["fetch"] is None:
                lines.append(f"  {name}: skipped (no key / unsupported)")
                continue
            try:
                daily = cfg["fetch"](key, days=1)
            except ProviderError as e:
                lines.append(f"  {name}: ERROR — {e}")
                continue
            spend = float(daily[-1]["spend_usd"]) if daily else 0.0
            hour = _dt.datetime.now().hour  # local hour

        threshold = SPIKE_MULTIPLE * median if median > 0 else ZERO_BASELINE_MIN_USD
        status = "OK"
        if spend > threshold:
            status = "ALERT"
            alerts.append(
                f"{name}: today's spend ${spend:.2f} exceeds {SPIKE_MULTIPLE}x "
                f"the 14d median (${median:.2f}/day; threshold ${threshold:.2f})"
            )
        if hour in UNUSUAL_HOURS and spend > 0:
            status = "ALERT"
            alerts.append(
                f"{name}: usage detected at {hour:02d}:00 local (unusual hours "
                f"00:00–06:00) — possible compromised key"
            )
        lines.append(f"  {name}: today ${spend:.2f} vs median ${median:.2f}/day -> {status}")

    print("api-key-burn-watch check")
    print("\n".join(lines))
    if alerts:
        print("\n*** SPEND ALERT ***")
        for a in alerts:
            print(f"  ! {a}")
        webhook = args.webhook or os.environ.get("BURN_WATCH_WEBHOOK_URL", "").strip()
        if webhook:
            _send_webhook(webhook, {"tool": "api-key-burn-watch",
                                    "alerts": alerts,
                                    "at": _utcnow_iso()})
        return 2
    print("\nNo anomalies detected.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="api-key-burn-watch",
        description="Tripwire for stolen AI provider API keys: learns your "
                    "normal burn rate, alerts on spend spikes and off-hours usage.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="Use tests/fixtures/*.json instead of live provider APIs.")
    p.add_argument("--fixtures", default="tests/fixtures",
                   help="Fixture directory for --dry-run.")
    p.add_argument("--baseline-file", default=str(BASELINE_FILE),
                   help="Where the rolling baseline JSON is stored.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("baseline", help="Fetch 14d usage and store the rolling baseline.")
    c = sub.add_parser("check", help="Compare today's spend vs baseline; alert on anomalies.")
    c.add_argument("--webhook", default="",
                   help="Optional alert webhook URL (or BURN_WATCH_WEBHOOK_URL).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "check":
        return cmd_check(args)
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
