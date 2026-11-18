# PlayBook — Turf & Badminton Court Slot Booker

A full-stack hourly sports-court booking demo built with React, FastAPI, SQLite and realtime slot updates. The app is runnable locally without any paid services or Supabase credentials. A Supabase schema and optional Realtime client hook are included for hosted deployments.

## Features

- Responsive React booking interface with venue and court selection.
- Badminton and football/futsal sample venues and courts seeded automatically.
- 17 hourly slots per court, from 06:00–23:00.
- Dynamic hourly pricing: +35% for 18:00–22:00 peak slots, +15% on weekends; surcharges stack.
- Atomic slot holds using SQLite `BEGIN IMMEDIATE`, a unique slot constraint, and expiring lock tokens (default 180 seconds).
- Booking confirmation checks the lock token and writes booking + demo payment in one transaction.
- Prevents double-bookings with both a live-lock uniqueness constraint and a booking uniqueness constraint.
- FastAPI WebSocket broadcasts slot changes to connected browsers, with a 9-second polling fallback.
- Booking history and simulated payment records. No real payment is taken.
- OpenAPI docs at `/docs`.

## Requirements

- Python 3.10+
- Node.js 18+ and npm

## Run locally

### 1. Start the API

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

The SQLite database is created automatically at `backend/turf_booker.db` and sample venues/courts are seeded on first launch. The API is available at http://localhost:8000 and docs at http://localhost:8000/docs.

Optional backend settings: copy `backend/.env.example` to `backend/.env` and adjust `DATABASE_PATH`, `LOCK_TTL_SECONDS` or `FRONTEND_ORIGIN`.

### 2. Start the React app

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite (normally http://localhost:5173).

Optional frontend settings: copy `frontend/.env.example` to `frontend/.env`. `VITE_API_URL` defaults to `http://localhost:8000`.

## Supabase Realtime / hosted deployment

`supabase/schema.sql` creates the hosted Postgres tables, uniqueness constraints and basic read policies. Run it in the Supabase SQL Editor, then enable Realtime for `slot_locks` and `bookings` in the Supabase dashboard. Add `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` to the frontend environment to enable the Realtime subscription hook.

**Important:** The out-of-the-box API uses SQLite as its authoritative transactional store and WebSockets for local realtime. The Supabase subscription hook is an optional deployment integration point; to make Supabase the authoritative store in production, configure the FastAPI service to use Supabase/Postgres and publish changes there. Never put a Supabase service-role key in the browser. The included SQL intentionally does not grant anonymous users access to private booking/payment records.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/venues` | Active venues |
| GET | `/api/courts?venue_id=1` | Active courts |
| GET | `/api/slots?court_id=1&slot_date=YYYY-MM-DD` | Slot state and price matrix |
| POST | `/api/locks` | Atomically hold an available slot |
| DELETE | `/api/locks/{token}` | Release a hold |
| POST | `/api/bookings` | Confirm a booking and create demo payment |
| GET | `/api/bookings` | Recent booking history |
| WS | `/ws` | Live slot-change events |

## Concurrency and lock behavior

1. When a player selects a free slot, the API starts an immediate SQLite write transaction.
2. Expired holds are deleted before checking availability.
3. The API checks for a confirmed booking and an existing live lock.
4. A unique `(court_id, slot_date, start_time)` constraint ensures only one active hold can exist for a slot.
5. The lock token expires after `LOCK_TTL_SECONDS` (180 seconds by default).
6. Confirmation validates the token and inserts the booking and payment atomically. The lock is removed in the same transaction.
7. Other browser sessions receive a WebSocket event and refresh availability.

## Demo payment note

The checkout records a `paid_demo` payment row to demonstrate the booking/payment flow. It is not connected to a payment gateway and does not charge a card or UPI account. Integrate a payment provider and verify its server-side webhook before using this for real transactions.

## Project structure

```text
backend/
  main.py                 FastAPI API, SQLite schema, locks and booking logic
  requirements.txt
  .env.example
frontend/
  index.html
  package.json
  .env.example
  src/main.jsx            React app and booking flow
  src/style.css           Responsive styling
supabase/
  schema.sql              Hosted Supabase Postgres schema
```
