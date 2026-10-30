# MyKeep

MyKeep is an ultra-lightweight, minimalist personal subscription and wishlist tracker inspired by Wallos, built with Django and SQLite (WAL mode) in a single self-contained container.

## Key Features & Improvements Over Wallos
- **Single-Container Lightweight Architecture:** Just like Wallos, MyKeep requires **zero external database, object storage, or cache servers**. It uses SQLite in WAL (`Write-Ahead Logging`) mode (`./data/mykeep.db`), local media volume storage (`./media`) for uploaded wishlist photos and subscription logos, and in-process memory caching (`LocMemCache`).
- **Minimalist, Responsive Design:** Flat monochrome Light and Dark modes (`#FFFFFF` / `#0A0A0A`) with crisp 1px borders, tabular numbers, desktop top bar, and mobile bottom navigation—free of gradients, glowing shadows, or cluttered status bars/pills.
- **Wishlist Tracker:** Keep track of items you want to buy (e.g., a MacBook Pro or Studio Display) by pasting an image link URL or uploading an image file, along with price, currency, product store URL, priority, target date, saved amount, and notes.
- **Accurate Multi-Currency & Payment History:** Every subscription renewal records a historical `SubscriptionPaymentRecord` and converts foreign currencies accurately into your primary currency using live Frankfurter (free, no API key required) or Fixer exchange rates.
- **1-Click Wallos Import / Export & iCal Feed:** Import existing Wallos JSON or CSV exports directly in Settings, export your data anytime, or subscribe to the RFC 5545 `.ics` calendar feed.

## Quick Start (Docker Compose — Single Container)

```bash
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:8000` in your browser and create your account. Your database and uploaded images are persisted on host folders `./data` and `./media`.

## Local Development

```bash
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py test
./venv/bin/python manage.py runserver
```

## Scheduled Daily Tasks

Run daily via cron to auto-advance due subscriptions, refresh exchange rates, and dispatch notifications:

```bash
python manage.py run_daily_tasks
```
