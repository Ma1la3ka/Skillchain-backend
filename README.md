# Skillchain
# SkillChain Backend

Skillchain helps artisans in Africa gain visibility for their skills and connects them 
with clients looking for trusted workers. This is the backend API powering that platform.

## Features
- [e.g. Artisan profiles & skill verification]
- [e.g. Client-artisan matching/search]
- [e.g. Escrow-based payments — funds auto-release after 24h if the client doesn't respond]
- [e.g. Ratings/reviews for completed jobs]

## Tech Stack
- Python, Flask
- [Your DB — Postgres/MySQL/SQLite? see database.py]
- APScheduler (or similar) for background jobs

## Project Structure
├── main.py              # App factory, CORS, blueprint registration
├── config.py            # App configuration (secrets, cookies, allowed origins)
├── database.py          # DB connection/setup
├── database_helper.py   # Query helpers
├── scheduler.py         # Background job: auto-release escrow after 24h
├── utils.py             # Shared utilities
└── routes/              # API endpoints (blueprints)

─ routes/              # API endpoints (blueprints)

## Setup
```bash
git clone https://github.com/Ma1la3ka/Skillchain-backend
cd Skillchain-backend
pip install -r requirements.txt
python main.py
```
