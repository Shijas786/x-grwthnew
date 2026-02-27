#!/bin/bash

# Force Playwright to use the /app cache for reliability
export PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright

echo "Ensuring Playwright browsers are installed..."
playwright install chromium

# Run the bot
echo "Starting Bot..."
python bot.py
