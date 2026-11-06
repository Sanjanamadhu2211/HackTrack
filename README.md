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
Postgres (local via docker compose, or Neon with a Neon DATABASE_URL).

## Tests

```
cd apps/api && python -m pytest -q
```
