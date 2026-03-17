# DeepEarn WhatsApp Renting Bot

A Telegram bot designed to manage WhatsApp account renting operations via DeepEarn. This bot allows users to efficiently create accounts (via Emailnator), link WhatsApp numbers, and automatically generate pairing QR codes to scan natively from WhatsApp.

## Features
- **Admin Approval System**: New users must be approved by the admin before they can use the bot.
- **Custom Passwords & Proxies**: Each user can set and test their own proxies (`IP:PORT:USER:PASS`) and define custom default passwords for accounts.
- **SAS & MAR Methods**:
  - **SAS (Single Account Strategy)**: Re-uses the same created account to link multiple WhatsApp numbers (generates new QR codes for the same Email).
  - **MAR (Multiple Accounts Rotation)**: Generates a completely new email and account using the same invite code.
- **Automatic Polling**: Automatically checks the WhatsApp linking status once the QR is generated and reports back on successful linkage.
- **Account View**: Shows the user a breakdown of all their unique linked emails with a count of how many WhatsApp accounts each email holds.

## Requirements
- Python 3.10+
- `pip install -r requirements.txt`

## Setup

1. Copy `.env.example` to `.env` and configure your credentials:
```env
BOT_TOKEN=your_telegram_bot_token_here
ADMIN_USER_ID=your_telegram_id_here
DEFAULT_PASSWORD=53561106Tojo
DATABASE_URL=postgresql://postgres:your_database_password@db.your-project-ref.supabase.co:5432/postgres
PORT=10000
```
2. Run the bot:
```bash
python tg_bot.py
```

## Supabase

Run `supabase_schema.sql` in your Supabase SQL editor before starting the bot.

## Render Docker Deploy

This repo includes `Dockerfile`, `render.yaml`, and `render_service.py` for Render deployment.

1. Push the repo to GitHub.
2. Create a Render Blueprint or Docker Web Service from the repo.
3. Add your env vars in Render.
4. Deploy the service.

The Render entrypoint opens `0.0.0.0:${PORT}` immediately and defaults to port `10000`, while the Telegram bot keeps running with polling in the same container.

## How to use
- Once started, the bot initializes the required tables in Supabase automatically.
- Any user who wants to join must press **Start** and the Admin will receive a notification to Approve or Reject them.
- Users can click **"My Account"** to see their linked accounts count. If they copy/paste any email from that list back into the chat, the bot will instantly resume the SAS mode for that exact email.

## File Map
- `tg_bot.py`: The main Telegram bot router and logic. Run this file.
- `render_service.py`: Render entrypoint that exposes a health endpoint and runs the bot 24/7.
- `bot_backend.py`: The core wrapper that interacts with Emailnator, DeepEarn, and WaLink APIs.
- `bot_requests.py`: Core client classes — `EmailnatorClient` and `DeepEarnClient` (account creation logic).
- `wa_link_gui.py`: `WaLinkClient` — handles WhatsApp QR linking via the DeepEarn WaLink service.
- `database.py`: Handles Supabase Postgres operations (user tracking, account tracking, and settings).
- `config.py`: Loads the `.env` configuration securely.

---
*Created for efficient DeepEarn WhatsApp rotation operations.*
