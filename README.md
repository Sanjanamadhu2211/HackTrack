# HackTrack

HackTrack keeps one clear next action for every hackathon you track: save
events from a link or pasted post, get Telegram reminders before every
deadline, and mark things done in one tap.

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

## Supabase database access

The API connects directly to PostgreSQL using its server-side database credentials.
Application tables must have row-level security enabled and no grants to the
Supabase anon or authenticated API roles; they are accessed through the API,
not the Supabase Data API. Apply these restrictions in the same transaction as
any migration that creates a table, and verify privileges before deploying.
Keep database credentials out of version control.
