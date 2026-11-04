# p2ptracker — USDT/MMK P2P rate tracker

Polls USDT↔MMK P2P ads on **Bybit and Bitget** every 5 seconds, stores rates in SQLite,
serves a historical dashboard, and sends **Telegram alerts** when a price crosses your thresholds.

- **BUY** = you buy USDT (merchants' sell ads; lower is better)
- **SELL** = you sell USDT (merchants' buy ads; higher is better)
- Per exchange/side it stores **best** price and **avg5** (mean of the top 5 ads after filters).

## Quick start (local)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux/macOS: .venv/bin/pip
cp .env.example .env                              # then fill in values
.venv/Scripts/uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

## Telegram setup

1. Talk to **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Start the app, open your bot in Telegram and send `/chatid`.
3. Put that number in `TELEGRAM_CHAT_ID` and restart. Commands only work from that chat.

| Command | |
|---|---|
| `/now` | current rates table, cheapest buy / best sell |
| `/alerts` | list rules |
| `/add bybit buy best <= 4850 [cooldown_sec]` | add rule (exchange may be `any`; metric `best` or `avg5`) |
| `/del 3`, `/on 3`, `/off 3` | delete / enable / disable rule |
| `/pause`, `/resume` | mute all alerts |

Alerts fire when the condition **becomes** true (crossing), then wait `cooldown_sec` (default 300) before
firing again for that rule + exchange. You also get one message if an exchange fails for
`FAIL_ALERT_AFTER` consecutive polls, and one when it recovers.

## Deploy on a VPS (Docker)

```bash
git clone <repo> p2ptracker && cd p2ptracker
cp .env.example .env && nano .env        # set TELEGRAM_*, DASHBOARD_USER/PASS
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
docker compose logs -f
```

### Reaching the dashboard

By default the port is bound to the VPS's `127.0.0.1` only. Either:

- **SSH tunnel** (nothing exposed): `ssh -L 8000:localhost:8000 root@<vps-ip>`, then open http://localhost:8000.
- **Public** at `http://<vps-ip>:8000`: in `.env` set `DASHBOARD_USER`, `DASHBOARD_PASS` and
  `BIND_ADDR=0.0.0.0`, then `docker compose up -d`. Allow TCP 8000 in any cloud firewall. This is plain
  HTTP (password sent unencrypted) — prefer HTTPS below.
- **HTTPS with your domain** (recommended): the bundled Caddy service gets a Let's Encrypt certificate
  automatically.
  1. DNS: add an **A record** for e.g. `p2p.example.com` → VPS IP. Check with `dig +short p2p.example.com`.
  2. Ports 80 and 443 must be free on the VPS (`ss -tlnp | grep -E ':(80|443) '`) and open in any cloud firewall.
  3. In `.env`: `COMPOSE_PROFILES=https`, `DOMAIN=p2p.example.com`, keep `DASHBOARD_USER`/`DASHBOARD_PASS`,
     and set `BIND_ADDR=127.0.0.1` (closes the plain-HTTP port).
  4. `docker compose up -d --force-recreate`, then `docker compose logs caddy` should show
     "certificate obtained successfully". Open https://p2p.example.com.

Before deploying, check both exchanges answer from the VPS (each should print MMK prices):

```bash
curl -s -X POST https://api2.bybit.com/fiat/otc/item/online -H 'Content-Type: application/json' -d '{"tokenId":"USDT","currencyId":"MMK","side":"1","size":"3","page":"1","amount":"","payment":[]}' | head -c 300; echo
curl -s -X POST https://www.bitget.com/v1/p2p/pub/adv/queryAdvList -H 'Content-Type: application/json' -d '{"side":1,"pageNo":1,"pageSize":3,"coinCode":"USDT","fiatCode":"MMK","languageType":0}' | head -c 300; echo
```

### Why Binance is off by default

The Binance adapter exists (`app/exchanges/binance.py`) but Binance only offers MMK on P2P to
logged-in accounts with Myanmar KYC. Anonymous requests get a region-based fiat list without MMK
(verified from JP, SG and a home connection), so polling returns no ads. Adding `binance` to
`EXCHANGES` is harmless — the dashboard just shows it as "No ads returned". Feeding the tracker your
Binance session cookies would work around this but is not supported: it puts a live session for
your real account on the server.

## Dashboard login & settings

- With `DASHBOARD_USER`/`DASHBOARD_PASS` set, the dashboard shows a sign-in page and keeps you signed in
  for 30 days (signed, HttpOnly cookie). Changing the password signs everyone out. Failed logins are
  limited to 10 per 15 minutes per IP. Scripts can still use HTTP Basic auth (`curl -u user:pass`).
- **Update every** (top bar) sets how often the exchanges are polled: 5 s minimum, up to 10 min. It
  applies immediately and is saved, overriding `POLL_INTERVAL` from `.env`.

## Data & retention

- `rate_snapshots`: one row per exchange/side per poll (~104k rows/day).
- Raw rows older than `RAW_RETENTION_DAYS` (30) are rolled into hourly OHLC (`rate_ohlc_1h`) and kept forever.
- History API picks resolution automatically: raw ≤6h, 1-min ≤2d, 5-min ≤14d, else hourly.

## Ad filters

Dashboard → *Ad filters*: per exchange, a trade amount in MMK (ad must accept it) and payment methods
(ad must offer at least one). Bybit only returns numeric payment IDs; they're translated via
`PAYMENT_NAMES` in `app/exchanges/bybit.py`. A new method shows up as `#<id>` — add it to that map.

## API

`GET /api/latest` · `GET /api/history?range=1h|6h|24h|7d|30d|90d|1y|all&exchange=&side=` (or `from`/`to` epoch) ·
`GET|POST /api/alerts` · `PATCH|DELETE /api/alerts/{id}` · `GET /api/alert-events` · `GET|PUT /api/settings` · `GET /healthz`

## Caveats

These are the exchanges' **unofficial public web endpoints**; they can change or rate-limit without
notice. Each exchange is isolated, so one breaking doesn't stop the others. Tests: `pytest`.
