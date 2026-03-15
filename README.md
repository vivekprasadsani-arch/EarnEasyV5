# EarnHeap / RupeeRunner Tools

## EarnHeap Telegram Bot

Install dependencies:

```bash
npm install
```

Run:

```bash
npm start
```

Required environment variables:

```bash
EARNHEAP_BOT_TOKEN=...
GOOGLE_SPREADSHEET_ID=1WzGkrTgEjchqgC2_ZImeyd-mFoiCobmU-MmTG3KXzDQ
GOOGLE_CREDENTIALS_JSON={...}
```

Features:

- Telegram long-polling bot with reply-keyboard menu.
- First-time users require admin approval.
- Approved users can submit WhatsApp numbers in common messy formats.
- Bot creates a fresh EarnHeap account, requests a pairing code, and shows the code with a copy button.
- Admin can set or clear an EarnHeap proxy, broadcast messages, remove users, and review pending approvals.
- Multiple users can use the bot at the same time without one user's long link job blocking others.
- Each link job keeps its own persisted session identity so restart/resume does not mix users or accounts.

Hidden commands:

```bash
/setpassword <value>
/setinvite <value>
/setproxy <url>
/clearproxy
/broadcast <text>
/removeuser <telegram_user_id>
/users
/cancel
```

Behavior:

- Admin using `/setpassword` or `/setinvite` changes the global default for everyone.
- Normal users using `/setpassword` or `/setinvite` create a personal change request.
- Personal changes are applied only to that user after admin approval.

Notes:

- Default password and invitation code are stored in bot state and hidden from normal users.
- Proxy setting is applied only to EarnHeap requests, not Telegram polling.
- State is persisted directly in Google Sheets.
- For Render web service deployment, the app also exposes an HTTP health endpoint on port `10000`.

## RupeeRunner CLI

Run:

```bash
node rupeerunner_bind_cli.mjs
```

Optional flags:

```bash
node rupeerunner_bind_cli.mjs --login-phone +919661806356 --password dollor1234 --target-phone +919097153825 --poll-interval 5 --timeout 300
```

Skip requesting a new code and only monitor bind status:

```bash
node rupeerunner_bind_cli.mjs --login-phone +919661806356 --password dollor1234 --target-phone +919097153825 --skip-code-request
```
