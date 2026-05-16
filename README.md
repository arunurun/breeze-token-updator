# breeze-token-updator

Token operations repository for ICICI Breeze session refresh.

This repo is intentionally separate from Titan and handles only:
- Daily Breeze token validation
- Expiry alert notification
- Secure token update endpoint integration (Supabase Edge Function included)
- Market-closed skip guard (weekends + NSE trading holidays)

## Daily user interaction

1. Scheduled workflow checks Breeze token health.
2. If expired, you receive a mobile alert with:
   - Breeze login URL
   - Token form URL (hosted HTML page)
3. You complete login + 2FA on mobile.
4. Open the token form URL, paste the API_Session (or full redirect URL), and submit.
5. Supabase Edge Function stores token in `session_config`.
6. Titan picks up the new token in the next run.

## Required GitHub secrets

- `BREEZE_API_KEY`
- `BREEZE_SECRET`
- `SUPABASE_URL`
- `SUPABASE_KEY` (service-role key value)
- `TOKEN_UPDATE_URL` (endpoint that accepts the new token)
- `TOKEN_FORM_URL` (HTML form URL, e.g. GitHub Pages)
- `STATE_SIGNING_SECRET` (HMAC secret used to sign/validate short-lived update links)
- `MARKET_HOLIDAYS_IST` (optional comma-separated `YYYY-MM-DD` list for additional market holidays/overrides)
- `ALERT_WEBHOOK_URL` (optional; Slack/WhatsApp bridge/custom notifier)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` (optional email alerts)

## Supabase Edge Function included

Function path:

- `supabase/functions/update-breeze-token/index.ts`

Behavior:

- GET with valid `state` renders an HTML form by default.
- GET with `format=json` returns JSON usage help.
- GET can also update token directly using `token_input` query parameter.
- POST accepts form submit or JSON `token_input` (raw `API_Session` or full redirect URL).
- Verifies signed `state` (expiry + scope) when `STATE_SIGNING_SECRET` is configured.
- Upserts token into `session_config` row `id=1`.

## Token form (HTML)

Static HTML form lives at `docs/index.html`. Host this with GitHub Pages:

1. GitHub repo → Settings → Pages
2. Source: `main` branch, folder `/docs`
3. Set secret `TOKEN_FORM_URL` to the Pages URL:
   `https://arunurun.github.io/breeze-token-updator/`

## Deploy function (GitHub Actions)

Workflow:

- `.github/workflows/deploy_update_function.yml`

Required additional secrets for deploy workflow:

- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_PROJECT_REF`

Run it manually from GitHub Actions once, then set:

- `TOKEN_UPDATE_URL=https://<project-ref>.functions.supabase.co/update-breeze-token`

## Security notes

- Keep `STATE_SIGNING_SECRET` long/random.
- Keep state TTL short (current default: 15 minutes).
- Do not share raw tokens in chat messages.
- Use only service-role secrets on server side (`SUPABASE_KEY` in Actions).

## Local run

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
python src/validate_token.py
```

## Schedule and market holiday behavior

- Workflow schedule is set to `06:00 IST` Monday-Friday (`30 0 * * 1-5` in UTC).
- Script exits early when market is closed for scheduled runs:
  - weekends (Saturday/Sunday)
  - NSE trading holidays fetched at runtime
  - optional additional dates from `MARKET_HOLIDAYS_IST`
- Manual GitHub dispatch (`workflow_dispatch`) always continues validation, even on weekends/holidays.
