import subprocess
import os
import sys

# Force Playwright to look in the correct directory for Chromium
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/root/.cache/ms-playwright"

# Force browser installation every time the container starts
playwright_path = os.path.join(os.path.dirname(sys.executable), "playwright")
print(f"Ensuring Playwright browsers are installed using: {playwright_path}")
subprocess.run([playwright_path, "install", "chromium"], check=True)
subprocess.run([playwright_path, "install-deps", "chromium"], check=True)

import json
import time
import random
import logging
import traceback
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from openai import OpenAI

# Load environment variables
load_dotenv()

# Configuration from environment variables
X_AUTH_TOKEN = os.getenv("X_AUTH_TOKEN", "").strip()
X_CT0 = os.getenv("X_CT0", "").strip()
OUR_USERNAME = os.getenv("OUR_USERNAME", "").strip()
TARGET_INFLUENCER_USERNAME = os.getenv("TARGET_INFLUENCER_USERNAME", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MAX_REPLIES_PER_DAY = int(os.getenv("MAX_REPLIES_PER_DAY", 20))
MAX_ENG_POSTS_PER_DAY = int(os.getenv("MAX_ENG_POSTS_PER_DAY", 3))
MAX_IMG_POSTS_PER_DAY = int(os.getenv("MAX_IMG_POSTS_PER_DAY", 1))
REPLY_LANGUAGE = os.getenv("REPLY_LANGUAGE", "English")

# Constants
PROCESSED_IDS_FILE = "processed_ids.json"
DAILY_COUNT_FILE = "daily_count.json"
LOG_FILE = "bot.log"
MIN_GAP_BETWEEN_REPLIES = 90  # Seconds
LAST_REPLY_TIME = 0
LAST_ENG_POST_TIME = 0

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE)
    ]
)
logger = logging.getLogger(__name__)

# Persistence Functions
def load_processed_ids():
    """Load processed tweet IDs from a file."""
    if os.path.exists(PROCESSED_IDS_FILE):
        try:
            with open(PROCESSED_IDS_FILE, 'r') as f:
                return set(json.load(f))
        except Exception as e:
            logger.error(f"Error loading processed IDs: {e}")
    return set()

def save_processed_ids(processed_ids):
    """Save processed tweet IDs to a file."""
    try:
        with open(PROCESSED_IDS_FILE, 'w') as f:
            json.dump(list(processed_ids), f)
    except Exception as e:
        logger.error(f"Error saving processed IDs: {e}")

def get_daily_counts():
    """Get all daily counts (replies, engagement posts, images)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    default = {"date": today, "replies": 0, "eng_posts": 0, "img_posts": 0}
    if os.path.exists(DAILY_COUNT_FILE):
        try:
            with open(DAILY_COUNT_FILE, 'r') as f:
                data = json.load(f)
                if data.get("date") == today:
                    # Upgrade old format if necessary
                    if "count" in data:
                        data["replies"] = data.pop("count")
                    for key in ["replies", "eng_posts", "img_posts"]:
                        data.setdefault(key, 0)
                    return data
        except Exception as e:
            logger.error(f"Error reading daily counts: {e}")
    return default

def increment_daily_count(counter_type):
    """Increment a specific counter in daily counts."""
    data = get_daily_counts()
    data[counter_type] += 1
    try:
        with open(DAILY_COUNT_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving daily count: {e}")
    return data[counter_type]

def is_recent(timestamp_str):
    """Check if the tweet was posted within the last 2 hours."""
    try:
        # X timestamps are usually like: 2024-01-15T12:34:56.000Z
        tweet_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return now - tweet_time < timedelta(hours=2)
    except Exception as e:
        logger.error(f"Error parsing timestamp {timestamp_str}: {e}")
        return False

# Humanized Delay and Limits
def humanized_delay():
    """Determine the delay before posting a reply. Returns False if we should skip."""
    # 10% chance to skip
    if random.random() < 0.10:
        logger.info("Random decision: skipping this reply opportunity.")
        return False
    
    # Base delay: 3 to 18 minutes
    delay = random.uniform(3*60, 18*60)
    
    # 20% chance for extra delay: 10 to 30 minutes
    if random.random() < 0.20:
        extra = random.uniform(10*60, 30*60)
        delay += extra
        logger.info(f"Adding extra human delay: {extra/60:.2f} minutes")
        
    logger.info(f"Humanized delay sequence: waiting {delay/60:.2f} minutes before reply...")
    return delay

async def poll_delay():
    """Delay between polling cycles: 4 to 12 minutes."""
    delay = random.uniform(4*60, 12*60)
    logger.info(f"Cycle complete. Sleeping for {delay/60:.2f} minutes before next poll.")
    await asyncio.sleep(delay)

# OpenAI Integration
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    logger.error("CRITICAL: OPENAI_API_KEY is not set. Please add it to your Railway environment variables.")

def openai_analyze_and_reply(tweet_text, author):
    """Use GPT-4 to analyze a tweet and generate a human-like reply."""
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY not set.")
        return {"should_reply": False, "reply": ""}
        
    prompt = f"""You are a smart, witty social media user who engages genuinely with tweets.

A user named @{author} posted this tweet:
{tweet_text}

Your tasks:
1. Decide if this tweet is worth replying to.
   Skip if it is: spam, adult content, political extremism, gibberish, 
   or a photo with no text context.

2. If worth replying, write ONE short reply that:
   - Sounds completely human and natural (NOT like a bot)
   - Adds value, is witty, insightful, or asks a genuine question
   - Is 1-2 sentences max
   - Does NOT start with Great post! or generic praise
   - Does NOT emoji
   - Does NOT mention you are an AI

Respond ONLY in this exact JSON format:
{{
  "should_reply": true or false,
  "reason": "brief reason",
  "reply": "your reply text or empty string"
}}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85,
            max_tokens=200
        )
        content = response.choices[0].message.content.strip()
        
        # Strip potential markdown code fences
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        
        result = json.loads(content.strip())
        return result
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return {"should_reply": False, "reply": ""}

def generate_engagement_content(with_image=False):
    """Generate engagement-focused tweet text and an optional image prompt."""
    prompt = """You are a smart, witty social media user. 
Generate ONE high-engagement tweet that adds value, asks a question, or shares an insight.
- Sounds human (NOT bot-like)
- No generic 'Great day!' stuff
- No emojis
- 1-2 sentences max
"""
    if with_image:
        prompt += "\nAlso, provide a short, descriptive prompt for DALL-E 3 to generate an image that complements this tweet."
    
    prompt += "\nRespond ONLY in this JSON format:\n{\n  \"tweet\": \"tweet text\",\n  \"image_prompt\": \"image prompt or empty string\"\n}"
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85,
            max_tokens=300
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"): content = content[7:]
        if content.endswith("```"): content = content[:-3]
        return json.loads(content.strip())
    except Exception as e:
        logger.error(f"Error generating engagement content: {e}")
        return None

def generate_dalle_image(prompt):
    """Generate an image using DALL-E 3."""
    try:
        logger.info(f"Generating DALL-E 3 image for prompt: {prompt}")
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        # Download the image
        img_data = requests.get(image_url).content
        filename = f"eng_post_{int(time.time())}.png"
        with open(filename, 'wb') as handler:
            handler.write(img_data)
        logger.info(f"Image saved to {filename}")
        return filename
    except Exception as e:
        logger.error(f"DALL-E error: {e}")
        return None

def upload_media(file_path):
    """Upload media to X and return the media_id."""
    # Use upload.twitter.com which is more stable for GraphQL media uploads
    base_url = "https://upload.twitter.com/i/api/1.1/media/upload.json"
    headers = {
        "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I7BeIg1De8k%3DUq7gSnUYohsYmy88vuW8u0AaSMVYmFcwDLUeJMoTakMGbBBLsw",
        "cookie": f"auth_token={X_AUTH_TOKEN}; ct0={X_CT0}",
        "x-csrf-token": X_CT0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        size = os.path.getsize(file_path)
        logger.info(f"Uploading {file_path} ({size} bytes)...")
        # 1. INIT
        params = {"command": "INIT", "total_bytes": size, "media_type": "image/png"}
        res = requests.post(base_url, headers=headers, params=params)
        media_id = res.json().get("media_id_string")
        if not media_id:
            logger.error(f"Media INIT failed: {res.text}")
            return None
        
        # 2. APPEND
        with open(file_path, 'rb') as f:
            segment_index = 0
            while True:
                chunk = f.read(4 * 1024 * 1024)
                if not chunk: break
                files = {"media": chunk}
                params = {"command": "APPEND", "media_id": media_id, "segment_index": segment_index}
                requests.post(base_url, headers=headers, params=params, files=files)
                segment_index += 1
        
        # 3. FINALIZE
        params = {"command": "FINALIZE", "media_id": media_id}
        res = requests.post(base_url, headers=headers, params=params)
        logger.info(f"Media upload finalized: {media_id}")
        return media_id
    except Exception as e:
        logger.error(f"Error uploading media: {e}")
        return None

def get_x_features():
    """Common features for X GraphQL API posts."""
    return {
        "communities_web_enable_mailing_list_emails": True,
        "creator_monetization_tweet_level_use_guise_is_eligible_enabled": True,
        "dms_blue_verified_ow_enabled": False,
        "executable_relatable_tweets_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "has_birdwatch_notes_enabled": False,
        "interactive_text_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "standard_tweet_ids_as_strings_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "verified_phone_label_enabled": False,
        "view_counts_everywhere_api_enabled": True
    }

def post_tweet(text, media_id=None, reply_to_id=None):
    """Post a tweet or reply using X's internal GraphQL API."""
    global LAST_REPLY_TIME, LAST_ENG_POST_TIME
    
    url = "https://x.com/i/api/graphql/SoVnbfCycZ7fERGCwpZkYA/CreateTweet"
    headers = {
        "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I7BeIg1De8k%3DUq7gSnUYohsYmy88vuW8u0AaSMVYmFcwDLUeJMoTakMGbBBLsw",
        "cookie": f"auth_token={X_AUTH_TOKEN}; ct0={X_CT0}",
        "x-csrf-token": X_CT0,
        "content-type": "application/json",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    media_entities = []
    if media_id:
        media_entities.append({"media_id": media_id, "tagged_users": []})

    variables = {
        "tweet_text": text,
        "dark_request": False,
        "media": {"media_entities": media_entities, "possibly_sensitive": False},
        "semantic_annotation_ids": []
    }
    
    if reply_to_id:
        variables["reply"] = {"in_reply_to_tweet_id": reply_to_id, "exclude_reply_user_ids": []}

    payload = {
        "variables": variables,
        "features": get_x_features(),
        "queryId": "SoVnbfCycZ7fERGCwpZkYA"
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            res_json = response.json()
            if "errors" in res_json:
                logger.error(f"X API errors: {res_json['errors']}")
                return False
            logger.info("X Post successful!")
            if reply_to_id: LAST_REPLY_TIME = time.time()
            else: LAST_ENG_POST_TIME = time.time()
            return True
        elif response.status_code == 429:
            logger.warning("Rate limit hit (429).")
            return False
        else:
            logger.error(f"X post failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error posting: {e}")
        return False

# post_reply is now merged into post_tweet, but keep wrapper for compatibility
def post_reply(reply_text, tweet_id):
    """Post a reply using X's internal GraphQL API."""
    global LAST_REPLY_TIME
    
    # Enforce minimum gap
    now = time.time()
    if now - LAST_REPLY_TIME < MIN_GAP_BETWEEN_REPLIES:
        wait_needed = MIN_GAP_BETWEEN_REPLIES - (now - LAST_REPLY_TIME)
        logger.info(f"Enforcing minimum gap. Waiting {wait_needed:.2f} seconds...")
        time.sleep(wait_needed)

    return post_tweet(reply_text, reply_to_id=tweet_id)

async def handle_engagement_posts():
    """Check if it's time to post an engagement tweet and do so if needed."""
    global LAST_ENG_POST_TIME
    
    counts = get_daily_counts()
    if counts["eng_posts"] >= MAX_ENG_POSTS_PER_DAY:
        return

    # Randomized scheduling: only post if at least 1-4 hours have passed since last post
    # and a random chance hits (to make it irregular)
    now = time.time()
    elapsed = now - LAST_ENG_POST_TIME
    
    # First post of the day doesn't need to wait for LAST_ENG_POST_TIME being large
    # but we should still wait at least some time after startup
    if LAST_ENG_POST_TIME == 0:
        if random.random() < 0.3: # 30% chance to post shortly after startup
            pass
        else:
            return

    # Random interval between 2 to 6 hours for subsequent posts
    wait_time = random.uniform(2*3600, 6*3600)
    if elapsed < wait_time:
        return

    # Decide if we want an image (1-2 per day limit)
    with_image = False
    if counts["img_posts"] < MAX_IMG_POSTS_PER_DAY:
        # Higher chance if we haven't posted any images yet today
        chance = 0.5 if counts["img_posts"] == 0 else 0.2
        if random.random() < chance:
            with_image = True

    logger.info(f"Starting engagement post cycle (with_image={with_image})")
    content = generate_engagement_content(with_image=with_image)
    if not content or not content.get("tweet"):
        return

    media_id = None
    if with_image and content.get("image_prompt"):
        img_file = generate_dalle_image(content["image_prompt"])
        if img_file:
            media_id = upload_media(img_file)
            # Cleanup local file
            try: os.remove(img_file)
            except: pass

    success = post_tweet(content["tweet"], media_id=media_id)
    if success:
        increment_daily_count("eng_posts")
        if media_id:
            increment_daily_count("img_posts")
        logger.info(f"Engagement post shared! Today's counts: {get_daily_counts()}")

async def scrape_tweet_content(page, tweet_url):
    """Visit a specific tweet URL and determine if it's a root tweet using multiple methods."""
    try:
        logger.info(f"Checking tweet details at {tweet_url}")
        await page.goto(tweet_url, wait_until="networkidle")
        await asyncio.sleep(5)  # Increased wait time for dynamic content
        
        # Method 3: Page title check
        title = await page.title()
        if "on X" in title and "Replying to" in title:
            logger.info(f"Method 3 (Title) match: '{title}' indicates it's a reply. Not root.")
            return {"is_root": False}
        
        # Extract tweet data from the first visible tweet element
        tweet_element = await page.query_selector('[data-testid="tweet"]')
        if not tweet_element:
            logger.warning("Could not find tweet element on the page.")
            return None
            
        inner_html = await tweet_element.inner_html()
        
        # Method 1 & 2: Check for markers of being a reply
        # A root tweet doesn't have "Replying to @username" above the main text space
        if 'div[dir="auto"]' in inner_html and ("Replying to" in inner_html or "En respuesta a" in inner_html):
            # Check if this "Replying to" is actually part of the tweet context
            # We look for the link that starts with "Replying to"
            reply_indicator = await tweet_element.query_selector('div:has-text("Replying to")')
            if reply_indicator:
                logger.info("Reply indicator found in DOM. Not root.")
                return {"is_root": False}
            
        # Extract metadata
        text_el = await tweet_element.query_selector('[data-testid="tweetText"]')
        text = await text_el.inner_text() if text_el else ""
        
        author_el = await tweet_element.query_selector('[data-testid="User-Name"]')
        author_text = await author_el.inner_text() if author_el else ""
        # Improved author extraction
        author = ""
        if "@" in author_text:
            # Usually format is "Name @username · Date"
            parts = author_text.split("@")
            if len(parts) > 1:
                author = parts[1].split("\n")[0].split("·")[0].strip()
        
        # Double check: if it's a root tweet, the URL should typically match the structure
        # but since we are visiting it directly, we just check if it's NOT a reply.
        logger.info(f"Confirmed root tweet by @{author}: {text[:50]}...")
        return {
            "is_root": True,
            "text": text,
            "author": author,
            "tweet_id": tweet_url.split("/")[-1]
        }
    except Exception as e:
        logger.error(f"Error scraping tweet content at {tweet_url}: {e}")
        return None

async def scrape_influencer_replies(browser_context, username):
    """Scrape the influencer's profile/with_replies page."""
    page = await browser_context.new_page()
    # Speed up page load by blocking media
    await page.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}", lambda route: route.abort())
    
    # Verify login by checking if 'Home' or profile link exists
    try:
        await page.goto("https://x.com/home", wait_until="networkidle")
        await asyncio.sleep(3)
        if await page.query_selector('[data-testid="SideNav_Account_Button"]'):
            logger.info("Login verified: Successfully authenticated with X.")
        else:
            # Check if we are redirected to login
            if "login" in page.url:
                logger.error("Login FAILED: Redirected to login page. Check X_AUTH_TOKEN and X_CT0.")
            else:
                logger.warning("Could not verify login button, but proceeding...")
    except Exception as e:
        logger.warning(f"Login verification check failed: {e}")

    replies_url = f"https://x.com/{username}/with_replies"
    logger.info(f"Visiting {replies_url}")
    
    try:
        await page.goto(replies_url, wait_until="networkidle")
        await asyncio.sleep(5)
        
        # Simulate human behavior
        for _ in range(3):
            await page.mouse.wheel(0, random.randint(500, 1000))
            await asyncio.sleep(random.uniform(1, 2))
            
        tweet_elements = await page.query_selector_all('[data-testid="tweet"]')
        logger.info(f"Found {len(tweet_elements)} tweet elements on page.")
        
        results = []
        for el in tweet_elements:
            try:
                # Robust reply detection: Look for 'Replying to' or specific link structure
                html = await el.inner_html()
                
                # Check for "Replying to" but be case-insensitive and handle potential locale variations
                # or better: look for an anchor tag that starts with "Replying to" or has /status/ parent links
                is_reply = False
                if "Replying to" in html or "En respuesta a" in html or "@" in html:
                    is_reply = True
                
                if not is_reply:
                    continue
                
                # Extract links to find parent tweet
                links = await el.query_selector_all('a[href*="/status/"]')
                status_ids = []
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        parts = href.split("/")
                        if "status" in parts:
                            status_ids.append(parts[parts.index("status") + 1])
                
                # Unique status IDs, sorted
                unique_ids = list(dict.fromkeys(status_ids))
                
                # If it's a reply by the influencer, we expect at least 2 unique status IDs:
                # 1. The parent tweet (at least one)
                # 2. The influencer's reply tweet itself
                if len(unique_ids) < 2:
                    continue
                    
                parent_id = unique_ids[0]
                tweet_id = unique_ids[-1]
                
                # Basic metadata
                time_el = await el.query_selector('time')
                timestamp = await time_el.get_attribute("datetime") if time_el else None
                
                results.append({
                    "parent_id": parent_id,
                    "influencer_reply_id": tweet_id,
                    "timestamp": timestamp
                })
            except Exception as e:
                logger.error(f"Error processing tweet element: {e}")
                
        await page.close()
        return results
    except Exception as e:
        logger.error(f"Error scrolling or finding tweets: {e}")
        await page.close()
        return []

async def main():
    """Main execution loop."""
    logger.info(f"Bot starting... Target: @{TARGET_INFLUENCER_USERNAME}")
    
    processed_ids = load_processed_ids()
    
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        
        # Inject cookies into context
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        await context.add_cookies([
            {"name": "auth_token", "value": X_AUTH_TOKEN, "domain": ".x.com", "path": "/", "secure": True, "httpOnly": True, "sameSite": "None"},
            {"name": "ct0", "value": X_CT0, "domain": ".x.com", "path": "/", "secure": True, "sameSite": "None"}
        ])
        
        while True:
            try:
                # 1. Handle Engagement Posts (randomly throughout the day)
                await handle_engagement_posts()

                # 2. Check daily limit for replies
                counts = get_daily_counts()
                if counts["replies"] >= MAX_REPLIES_PER_DAY:
                    logger.info(f"Daily reply limit ({MAX_REPLIES_PER_DAY}) reached. Checking again later...")
                    await asyncio.sleep(1800) # Check every 30 mins
                    continue

                # 3. Scrape influencer's replies
                influencer_replies = await scrape_influencer_replies(context, TARGET_INFLUENCER_USERNAME)
                logger.info(f"Scraped {len(influencer_replies)} potential reply chains.")
                
                for item in influencer_replies:
                    parent_id = item["parent_id"]
                    
                    # 1. Skip if already processed
                    if parent_id in processed_ids:
                        continue
                    
                    # 2. Skip if influencer's reply is too old (> 2 hours)
                    if not is_recent(item["timestamp"]):
                        logger.info(f"Skipping tweet {parent_id}: Too old.")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue

                    # 3. Visit parent tweet to confirm it's a ROOT tweet
                    parent_url = f"https://x.com/i/status/{parent_id}"
                    temp_page = await context.new_page()
                    root_data = await scrape_tweet_content(temp_page, parent_url)
                    await temp_page.close()
                    
                    if not root_data or not root_data.get("is_root"):
                        logger.info(f"Skipping {parent_id}: Not a root tweet.")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue
                        
                    # 4. Safety Filters
                    author = root_data["author"]
                    if author.lower() == TARGET_INFLUENCER_USERNAME.lower():
                        logger.info(f"Skipping {parent_id}: Author is the influencer themselves.")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue
                    
                    if author.lower() == OUR_USERNAME.lower():
                        logger.info(f"Skipping {parent_id}: Author is us.")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue
                    
                    if not root_data["text"].strip():
                        logger.info(f"Skipping {parent_id}: Content is empty.")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue

                    # 5. OpenAI Analysis
                    logger.info(f"Analyzing root tweet by @{author}: {root_data['text'][:100]}...")
                    analysis = openai_analyze_and_reply(root_data["text"], author)
                    
                    if not analysis.get("should_reply"):
                        logger.info(f"OpenAI skip: {analysis.get('reason')}")
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue

                    # 6. Humanized Delay
                    wait_time = humanized_delay()
                    if wait_time is False: # Decision to skip
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        continue
                    
                    await asyncio.sleep(wait_time)
                    
                    # 7. Post Reply
                    success = post_reply(analysis["reply"], parent_id)
                    if success:
                        processed_ids.add(parent_id)
                        save_processed_ids(processed_ids)
                        new_count = increment_daily_count("replies")
                        logger.info(f"Reply posted successfully! Daily count: {new_count}/{MAX_REPLIES_PER_DAY}")
                    
                # End of cycle delay
                await poll_delay()
                
            except Exception as e:
                logger.error(f"CRITICAL ERROR in main loop:\n{traceback.format_exc()}")
                await asyncio.sleep(60) # Short sleep before retry

if __name__ == "__main__":
    asyncio.run(main())
