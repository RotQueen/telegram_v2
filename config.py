"""Configuration and constants for the Telegram relay bot.

Environment variables expected (suitable for Railway):
- BOT_TOKEN: Telegram bot token (required).
- ADMIN_USER_ID: Telegram user ID for the admin (optional, defaults to Tanya's ID 5386753143).
- DATABASE_PATH: Path to SQLite database file (optional, defaults to './projects.db').

The bot runs with long polling (no webhooks) as recommended for Railway free tier.
"""
from __future__ import annotations

import os

DEFAULT_ADMIN_USER_ID = 5386753143

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

try:
    ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", DEFAULT_ADMIN_USER_ID))
except ValueError as exc:
    raise RuntimeError("ADMIN_USER_ID must be an integer") from exc

DATABASE_PATH = os.environ.get("DATABASE_PATH", "./projects.db")
