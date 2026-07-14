<div align="center">

# Upwork Job Monitor — Discord Bot

**Real-time job feed monitoring, keyword routing, and Discord notifications — built on an async, crash-resilient architecture.**

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-async-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Playwright](https://img.shields.io/badge/Playwright-chromium-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-D71F00?logo=sqlite&logoColor=white)](https://www.sqlalchemy.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## Overview

This service polls Upwork job search feeds on a configurable interval, deduplicates results against a persistent store, classifies each posting by keyword, and delivers formatted alerts to the appropriate Discord channels — with automatic thread creation for longer job descriptions. It's built to run unattended: credential refresh, error recovery, and logging are all handled internally.

---

## Table of Contents

- [Key Features](#key-features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration Reference](#configuration-reference)
- [Bot Commands](#bot-commands)
- [Operational Notes](#operational-notes)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Key Features

| | |
|---|---|
| **Lightweight polling core** | Job retrieval runs over direct HTTP requests to Upwork's public GraphQL endpoint — browser automation is invoked only for periodic session-token refresh, keeping the steady-state footprint minimal. |
| **Rich Discord delivery** | New postings are rendered as color-coded embed cards; long descriptions automatically spawn dedicated discussion threads to work around Discord's message-length limits. |
| **Keyword-based routing** | Job titles and descriptions are parsed against a configurable keyword map and routed to distinct channels (e.g. Python, QA, React). |
| **Durable state** | A SQLAlchemy-backed SQLite store tracks every seen job, preventing duplicate posts across restarts. |
| **Self-recovery** | Expired sessions (HTTP 401/403) are detected automatically; the credential cache is cleared and refreshed without manual intervention or downtime. |
| **Production-grade logging** | Rotating file handlers (10MB cap) plus console output, with UTF-8 support on Windows. |

---

## Architecture

```
├── app/
│   ├── __init__.py
│   ├── config.py       # Environment + configuration loader
│   ├── database.py     # SQLAlchemy models and session management
│   ├── discord.py       # Bot client, embeds, background tasks, commands
│   ├── logger.py        # Logging configuration (console + rotating file)
│   └── scraper.py       # GraphQL client and session credential refresh
├── data/                 # SQLite database
├── logs/                 # Rotating application logs
├── .env                  # Local environment secrets (gitignored)
├── main.py               # Entry point
└── session.json          # Cached session credentials
```

**Flow:** `scraper` refreshes session credentials on a fixed interval → polls configured search feeds → new results are diffed against `database` → matches are classified and dispatched through `discord` → all stages report through `logger`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| Bot framework | discord.py (Discord Gateway API) |
| Data source | Upwork GraphQL API |
| Session handling | Playwright (Chromium) |
| HTTP client | `requests` |
| Persistence | SQLAlchemy ORM over SQLite |
| Configuration | `python-dotenv` |

---

## Getting Started

### Prerequisites

- Python 3.9 or later
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Git

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/upwork-job-monitor-bot.git
cd upwork-job-monitor-bot

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### Configure

Create a `.env` file in the project root — see [Configuration Reference](#configuration-reference).

### Run

```bash
python main.py
```

---

## Configuration Reference

All configuration is supplied via a `.env` file in the project root.

| Variable | Description | Example |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot authentication token | `your_discord_bot_token` |
| `DISCORD_CHANNEL_MAP` | JSON map of keyword → Discord channel ID | `{"default": 123456789, "python": 987654321}` |
| `UPWORK_SEARCH_URLS` | One or more Upwork job search feed URLs to monitor | `https://www.upwork.com/jobs/search/?q=python&sort=recency` |
| `MONITOR_INTERVAL` | Polling interval, in seconds | `300` |
| `BROWSER_MODE` | Playwright launch mode | `headless` |
| `DATABASE_URL` | SQLAlchemy connection string | `sqlite:///data/jobs.db` |

> **Security note:** `.env` is excluded via `.gitignore` by default. Never commit credentials or tokens.

---

## Bot Commands

| Command | Description |
|---|---|
| `!status` | Reports total jobs tracked, monitor loop state, and the active channel map |
| `!force_check` | Triggers an immediate feed check outside the normal polling interval |
| `!list_urls` | Lists all currently monitored search feed URLs |

---

## Operational Notes

- The bot polls only publicly accessible job search results and does not automate bidding, messaging, or any authenticated account actions.
- Polling interval and request patterns are intentionally conservative; adjust `MONITOR_INTERVAL` responsibly.
- Review Upwork's Terms of Service before deployment — you are responsible for ensuring your usage complies with them.

---

## Roadmap

- [ ] Multi-server Discord support
- [ ] Web dashboard for keyword/channel management
- [ ] Job history analytics and reporting
- [ ] Docker deployment support

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "Add your feature"`
4. Push the branch: `git push origin feature/your-feature`
5. Open a pull request

For significant changes, please open an issue first to discuss scope.

---

## License

Released under the [MIT License](LICENSE).