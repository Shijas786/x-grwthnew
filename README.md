# X Reply Bot

A Python bot designed to grow an X account by intelligently mimicking a target influencer's reply behavior. It uses Playwright for scraping, OpenAI for generating human-like responses, and X's internal GraphQL API for posting.

## Features

- **Playwright Scraping**: Mimics human browsing to extract reply activity without needing the official X API.
- **Root Tweet Detection**: Uses 5 different methods to ensure we only reply to the original/root tweet.
- **Automated Engagement**: Posts 1-5 unique, AI-generated tweets daily at random intervals.
- **DALL-E Integration**: Automatically generates and uploads images for 1-2 engagement posts daily.
- **Humanized Delays**: Random wait times and skip chances for both replies and posts to avoid detection.
- **Daily Limits**: Keeps total daily replies within a safe range.
- **Persistence**: Tracks processed IDs and daily counts to survive restarts.
- **Railway Ready**: Optimized for 24/7 execution on Railway.

## Configuration

1. **Get X Cookies**:
   - Log in to [x.com](https://x.com) in your browser.
   - Open DevTools (F12 or Right Click -> Inspect).
   - Go to the **Application** tab -> **Cookies** -> `https://x.com`.
   - Copy the values for `auth_token` and `ct0`.

2. **Setup .env**:
   - Copy `.env.example` to `.env`.
   - Fill in your cookies, X username, target influencer username, and OpenAI API key.

3. **Install Dependencies (Local Testing)**:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

## Engagement & Image Posting

The bot is configured to share standalone "Engagement Posts" to grow your following.
- **Total Posts**: 1-5 per day (controlled by `MAX_ENG_POSTS_PER_DAY`).
- **With Images**: 1-2 of those posts will feature AI images (controlled by `MAX_IMG_POSTS_PER_DAY`).
- **Timing**: Randomized intervals between 2 to 6 hours.

## Deployment on Railway

1. Connect your GitHub repository to Railway.
2. Railway will automatically detect the `nixpacks.toml` and `Procfile`.
3. Add all variables from your `.env` to the Railway project settings.
4. Deploy the service as a **Worker**.

## Safety Mechanism Overview

- **Random Slopes**: Randomly skips 10% of valid opportunities.
- **Time Window**: Only replies to tweets posted within the last 2 hours.
- **Duplicate Prevention**: IDs are tracked in `processed_ids.json`.
- **Content Filter**: OpenAI checks for spam, adult content, or extremism.
- **Internal API**: Uses the same GraphQL endpoint as the web app for maximum stealth.
