# MyKeep 📦

MyKeep is an ultra-lightweight, self-hosted personal subscription and wishlist tracker built with Django and SQLite (WAL mode) designed to run as a single, self-contained container.

## ✨ Key Features

- **Single-Container Lightweight Architecture:** Zero external database, object storage, or Redis/cache server dependencies. Uses SQLite in WAL (`Write-Ahead Logging`) mode (`./data/mykeep.db`), local media volume storage (`./media`) for photos and logos, and fast in-memory caching.
- **Clean, Minimalist Interface:** Thoughtful monochrome Light and Dark themes (`#FFFFFF` / `#0A0A0A`) with crisp borders, tabular numbers, desktop top bar, and mobile navigation bar—zero clutter, distraction-free.
- **Smart Wishlist Tracker:** Track purchases you are planning or saving toward with image attachments, priority tags, target purchase dates, price, savings progress, and direct store links.
- **Accurate Multi-Currency & Payment History:** Track recurring costs across currencies with automated foreign exchange rate updates via Frankfurter and Fixer. Every renewal maintains complete historical payment records.
- **Data Portability & Calendar Sync:** Export or import your data anytime via standard JSON and CSV, and sync renewal schedules directly into Apple Calendar, Google Calendar, or Outlook via RFC 5545 `.ics` feeds.

## 🚀 Quick Start (Docker Compose)

```bash
cp .env.example .env
docker compose up -d --build
```

Access the app at `http://localhost:8000`. Persistent database and uploaded media are mapped to local `./data` and `./media` directories.

## 🛠️ Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py test
python manage.py runserver
```

## ⏰ Scheduled Daily Tasks

Run daily via cron to auto-advance due subscriptions, refresh exchange rates, and trigger renewal reminders:

```bash
python manage.py run_daily_tasks
```

## 📄 License

This project is licensed under the [MIT License](LICENSE.md).
