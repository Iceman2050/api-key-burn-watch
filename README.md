# api-key-burn-watch

A tripwire for stolen AI provider API keys: polls **your own** usage & billing APIs, learns your normal daily burn rate, and fires an instant alert on spend spikes or off-hours usage — catching a compromised key before the invoice does.

**News hook (2026-10-17):** the OpenCode breach disclosure revealed a "large scale distillation attack" plus unauthorized access to a legacy system that exposed provider keys in 1.4% of Console workspaces — victims saw surprise charges (**$281 OpenAI / $168 Anthropic / $1,700** reports) *before* the disclosure. Source: [The Latent](https://thelatent.co/models/opencode-says-breach-tied-to-distillation-attack-exposed-users-ai-provider-keys)

## Install

Zero dependencies (Python 3.8+ stdlib only):

```bash
git clone https://github.com/Iceman2050/api-key-burn-watch.git
cd api-key-burn-watch
cp .env.example .env   # then add your keys — .env is gitignored, never commit keys
```

## Usage

```bash
export OPENAI_API_KEY="sk-..."   # admin key required for the costs endpoint

# 1. Learn your baseline (last 14 days of spend, stored in ~/.api-key-burn-watch.json)
python3 api_key_burn_watch.py baseline
# Baseline written to /home/you/.api-key-burn-watch.json
#   openai: 14d, median $0.48/day, max $0.61/day

# 2. Check today's burn (run from cron every 15–60 min)
python3 api_key_burn_watch.py check
# api-key-burn-watch check
#   openai: today $0.50 vs median $0.48/day -> OK
# No anomalies detected.                       # exit 0

# On a spike:
#   openai: today $281.00 vs median $0.48/day -> ALERT
# *** SPEND ALERT ***
#   ! openai: today's spend $281.00 exceeds 3.0x the 14d median (...)   # exit 2
```

Alert rules: **spend > 3× the 14-day median** (or ≥ $1.00 from a $0 baseline), or **any usage between 00:00–06:00 local**. Alerts print to stdout and optionally POST JSON to a webhook (`--webhook URL` or `BURN_WATCH_WEBHOOK_URL`).

No keys? `--dry-run` uses the bundled fixtures so you can demo and CI-test with zero credentials:

```bash
python3 api_key_burn_watch.py --dry-run baseline
python3 api_key_burn_watch.py --dry-run check
```

## Cron example

```cron
*/30 * * * * /usr/bin/python3 /opt/api-key-burn-watch/api_key_burn_watch.py check >> /var/log/burn-watch.log 2>&1
```

## Honest scope notes

- **OpenAI** is polled via the documented organization *costs* endpoint — it needs an **admin** API key. A regular project key returns 401/403 and is recorded as `unavailable`, not fatal.
- **Anthropic** has no public usage/billing API for API keys; **Google AI Studio** has no public billing endpoint for keys. The tool records both honestly as unsupported — use those providers' email spend alerts instead.
- This watches *your own* keys from *your* machine. It never sends data anywhere except a webhook *you* configure.

## Tests

```bash
python3 -m pytest tests/ -v   # 13 passed (live-provider paths use fixtures/mocks)
```

## License

MIT — see [LICENSE](LICENSE).

## Built by Ingenious AI

**api-key-burn-watch** is built by [Ingenious AI](https://github.com/Iceman2050).

Get early access to our 11 AI security & productivity platforms:

- [DisclosureGuard](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20DisclosureGuard.)
- [ModelMinder](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20ModelMinder.)
- [TermsWatch](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20TermsWatch.)
- [Vibe-Code Audit](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20Vibe-Code%20Audit.)
- [Rogue-Agent Defense](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20Rogue-Agent%20Defense.)
- [BreachHunter](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20BreachHunter.)
- [ChatBreaker](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20ChatBreaker.)
- [Agent Flight Recorder](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20Agent%20Flight%20Recorder.)
- [Agent Auth Gateway](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20Agent%20Auth%20Gateway.)
- [DomainGuard](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20DomainGuard.)
- [ContentCraft AI](https://wa.me/5016691053?text=Hi%20Ingenious%20AI!%20I%20want%20early%20access%20to%20ContentCraft%20AI.)
