# HackTrack

HackTrack keeps one clear next action for every hackathon you track: capture
event links from Discord or the web app, get Discord reminders before
deadlines, and mark things done in one tap.

## Run it locally

```
cp .env.example .env
make db
make migrate
make dev
```

That starts the Python API on http://localhost:8080 and the web app on
http://localhost:3000. The API runs FastAPI on uvicorn, talking to
Postgres (local via docker compose, or Supabase using its direct PostgreSQL
connection as DATABASE_URL). Direct Supabase connections require IPv6 access.

## Tests

```
cd apps/api && python -m pytest -q
```

## Discord setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add its bot, and invite it to your server with the `bot` and `applications.commands` scopes and permission to send messages.
2. Set `DISCORD_APPLICATION_ID`, `DISCORD_PUBLIC_KEY`, and `DISCORD_BOT_TOKEN` in the API environment. Set the existing `CRON_SECRET` too; the capture queue and daily discovery digest use the scheduled worker routes.
3. In the application settings, set the Interactions Endpoint URL to `https://YOUR_APP_HOST/api/integrations/discord/interactions`.
4. With those values in the project `.env`, run `cd apps/api && python register_discord_commands.py` to register `/link`, `/track`, and the **Track link** message action.
5. Open HackTrack Settings, choose **Connect Discord**, then run `/link` with the one-time code in the channel where you want HackTrack updates.

Use `/track URL` or choose **Apps → Track link** on a Discord message that contains a hackathon link. With the included Vercel Hobby schedule and Settings timezone set to `Asia/Kolkata`, the discovery brief runs daily between 9:30 and 10:30 a.m. and sends up to five links that have not already been sent or tracked. The [Hackathon Aggregator API](https://github.com/0xarchit/hackathon-api) supplies listings with platform, mode, and dates when available; direct listings provide a fallback and supplement missing MLH events. Use **Track** to save a link, or **Registered** after you register to save it and record your registration. Captured event messages also have an **I registered** button. Review and confirm extracted deadlines before their reminders are enabled; if a page cannot be read, add its deadlines in HackTrack. Captures are processed daily, so they can take up to a day. No Message Content Intent is needed.

Discovery treats matching normalized URLs or matching descriptive titles as the same event; different events with identical titles may be hidden. Reminder checks run only daily on Hobby, so time-sensitive reminders can expire before they are checked. Use an external scheduler or a plan supporting frequent cron jobs for reliable reminders and faster captures. The discovery schedule is fixed in UTC and must be adjusted together with the morning window if the account uses a different timezone.

The existing Telegram link data and webhook remain available during the cutover. Reminders use Discord after it is linked and can still use an existing Telegram link before that first Discord connection; disconnecting Discord stops new Discord deliveries.

## Supabase database access

The API connects directly to PostgreSQL using its server-side database credentials.
Application tables must have row-level security enabled and no grants to the
Supabase anon or authenticated API roles; they are accessed through the API,
not the Supabase Data API. Apply these restrictions in the same transaction as
any migration that creates a table, and verify privileges before deploying.
Keep database credentials out of version control.
