import os
import json
import logging
import asyncio
from dotenv import load_dotenv
import httpx
import re # Import re for URL validation
from aiohttp import web # Import aiohttp for web server

from utils.solana_rpc import get_sol_balance, get_transaction_count
from utils.telegram_bot import send_telegram_message

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
POLLING_INTERVAL_SECONDS = int(os.getenv("POLLING_INTERVAL_SECONDS", 300))

PUMP_FUN_API_URL = "https://pump.fun/api/market.json"

# Commercial Token Metadata API configuration
COMMERCIAL_METADATA_API_URL = os.getenv("COMMERCIAL_METADATA_API_URL")
COMMERCIAL_API_KEY = os.getenv("COMMERCIAL_API_KEY")

PROCESSED_TOKENS_FILE = "data/processed_tokens.json"

# Semaphore for rate limiting commercial API calls
CONCURRENT_METADATA_FETCHES = int(os.getenv("CONCURRENT_METADATA_FETCHES", 5)) # Limit to 5 concurrent fetches
metadata_semaphore = None # Will be initialized in main_loop

# Health check server configuration
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", 8080))
ENABLE_HEALTH_CHECK = os.getenv("ENABLE_HEALTH_CHECK", "false").lower() == "true"

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_processed_tokens():
    """Loads the set of processed token mint addresses from a file."""
    if os.path.exists(PROCESSED_TOKENS_FILE):
        try:
            with open(PROCESSED_TOKENS_FILE, 'r') as f:
                return set(json.load(f))
        except json.JSONDecodeError:
            logging.warning(f"Error decoding {PROCESSED_TOKENS_FILE}. Starting with empty set.")
            return set()
    return set()

def save_processed_tokens(tokens_set):
    """Saves the set of processed token mint addresses to a file."""
    try:
        with open(PROCESSED_TOKENS_FILE, 'w') as f:
            json.dump(list(tokens_set), f, indent=4)
    except IOError as e:
        logging.error(f"Error saving processed tokens to file: {e}")

async def fetch_new_pump_fun_tokens():
    """Fetches new token listings from pump.fun asynchronously."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(PUMP_FUN_API_URL, timeout=15.0)
            response.raise_for_status()
            try:
                return response.json()
            except json.JSONDecodeError:
                logging.error(f"Failed to parse JSON response from pump.fun API. Response text: {response.text[:200]}...")
                return []
    except httpx.TimeoutException:
        logging.error("Timeout fetching tokens from pump.fun API.")
        return []
    except httpx.RequestError as e:
        logging.error(f"Error fetching tokens from pump.fun: {e}")
        return []

# URL validation regex (basic, can be improved)
URL_REGEX = re.compile(
    r'^(?:http|ftp)s?://' # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' # domain...
    r'localhost|' # localhost...
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|' # ...or ip
    r'\[?[A-F0-9]*:[A-F0-9:]+\]?)' # ...or ip6
    r'(?::\d+)?' # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)

def is_valid_url(url: str) -> bool:
    """Basic validation for a URL."""
    return bool(URL_REGEX.match(url))

async def get_token_metadata_enriched(mint_address: str) -> dict | None:
    """
    Fetches enriched token metadata (including social links) from a commercial API,
    with rate limiting.
    """
    global metadata_semaphore # Use the global semaphore

    async with metadata_semaphore: # Acquire the semaphore before making the API call
        if not COMMERCIAL_METADATA_API_URL:
            logging.warning("COMMERCIAL_METADATA_API_URL is not set. Cannot fetch enriched metadata.")
            return None

        headers = {"Accept": "application/json"}
        if COMMERCIAL_API_KEY:
            headers["X-API-Key"] = COMMERCIAL_API_KEY 
        
        # Adjust payload based on your chosen API
        # Example for Helius 'queryMetadataV1' (note: check current API docs, this might be deprecated)
        payload = {
            "mintAccounts": [mint_address],
            "includeOffChain": True # Crucial for getting social links from external URIs
        }
        
        try:
            async with httpx.AsyncClient() as client:
                # Adjust the method and URL structure based on your chosen API
                response = await client.post(COMMERCIAL_METADATA_API_URL, json=payload, headers=headers, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                # --- START: CUSTOM PARSING LOGIC FOR YOUR API RESPONSE ---
                # You'll need to adjust this based on the actual API response structure.
                # This is a generic attempt to find common fields.
                metadata = None
                if data and isinstance(data.get("result"), list) and len(data["result"]) > 0:
                    # Assuming the first result is for our mint
                    api_metadata = data["result"][0] 
                    
                    # Example for extracting links from a Helius-like structure or similar API
                    telegram_url = None
                    twitter_url = None

                    # Try direct keys from the API's top level
                    telegram_url = api_metadata.get("telegram_url") or api_metadata.get("telegram")
                    twitter_url = api_metadata.get("twitter_url") or api_metadata.get("twitter")

                    # If the API returns raw off-chain JSON under a key (e.g., 'offChainData' or 'content.json_parsed')
                    # you might need to dig deeper:
                    # off_chain_data = api_metadata.get("offChainData") or api_metadata.get("content",{}).get("json_parsed")
                    # if off_chain_data:
                    #     telegram_url = telegram_url or off_chain_data.get("extensions", {}).get("telegram") \
                    #                     or off_chain_data.get("links", {}).get("telegram") \
                    #                     or next((attr['value'] for attr in off_chain_data.get('attributes', []) if attr.get('trait_type') and attr['trait_type'].lower() == 'telegram'), None)
                    #     twitter_url = twitter_url or off_chain_data.get("extensions", {}).get("twitter") \
                    #                   or off_chain_data.get("links", {}).get("twitter") \
                    #                   or next((attr['value'] for attr in off_chain_data.get('attributes', []) if attr.get('trait_type') and attr['trait_type'].lower() == 'twitter'), None)
                                        
                    metadata = {
                        "telegram_url": telegram_url,
                        "twitter_url": twitter_url,
                        # Add other fields you might want
                    }
                # --- END: CUSTOM PARSING LOGIC FOR YOUR API RESPONSE ---
                return metadata

        except httpx.TimeoutException:
            logging.error(f"Timeout fetching enriched metadata for {mint_address} from commercial API.")
            return None
        except httpx.RequestError as e:
            logging.error(f"Error fetching enriched metadata for {mint_address} from commercial API: {e}")
            return None
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON response from commercial metadata API for {mint_address}.")
            return None
        except Exception as e:
            logging.error(f"Unexpected error in get_token_metadata_enriched for {mint_address}: {e}")
            return None

def format_token_message(token_data, creator_balance_sol, creator_tx_count, telegram_link="Not found", twitter_link="Not found"):
    """Formats the token information into a human-readable message."""
    name = token_data.get("name", "N/A")
    symbol = token_data.get("symbol", "N/A")
    mint = token_data.get("mint", "N/A")
    creator = token_data.get("creator", "N/A")
    total_supply = token_data.get("total_supply", 0)

    formatted_supply = f"{total_supply:,.0f}" if isinstance(total_supply, (int, float)) else "N/A"

    # --- Smart Tags ---
    dev_wallet_empty_tag = ""
    if creator_balance_sol is not None and creator_balance_sol < 1:
        dev_wallet_empty_tag = "🚩 Dev Wallet Empty"

    wallet_history_tag = ""
    if creator_tx_count is not None and creator_tx_count > 20:
        wallet_history_tag = "🟢 Wallet Has Decent History"

    # Bonus: Optional emoji tags for meme vs utility tokens based on name
    meme_emojis = ["🐶", "🐱", "🐸", "🌚", "🚀", "💎"]
    utility_emojis = ["⚙️", "🛠️", "💡", "🔗"]
    
    token_name_lower = name.lower()
    name_emoji = ""
    if any(meme_keyword in token_name_lower for meme_keyword in ["dog", "cat", "pepe", "elon", "moon", "coin", "inu", "shiba", "meme"]):
        name_emoji = meme_emojis[0]
    elif any(utility_keyword in token_name_lower for utility_keyword in ["protocol", "solution", "network", "defi", "labs"]):
        name_emoji = utility_emojis[0]

    # Using MarkdownV2, so special characters need to be escaped
    def escape_markdown_v2(text):
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(['\\' + char if char in escape_chars else char for char in text])

    name_escaped = escape_markdown_v2(name)
    symbol_escaped = escape_markdown_v2(symbol)
    mint_escaped = escape_markdown_v2(mint)
    creator_escaped = escape_markdown_v2(creator)
    telegram_link_escaped = escape_markdown_v2(telegram_link)
    twitter_link_escaped = escape_markdown_v2(twitter_link)
    dev_wallet_empty_tag_escaped = escape_markdown_v2(dev_wallet_empty_tag)
    wallet_history_tag_escaped = escape_markdown_v2(wallet_history_tag)


    message = f"""
*{name_emoji} {name_escaped} \\(${symbol_escaped}\\)*

🪙 CA: `{mint_escaped}`
🎯 Exchange: `pump\\.fun`
👨🏻‍💻 Owner: `{creator_escaped}`

💰 Supply: {formatted_supply}

👨🏻‍💻 Creator info: `{creator_escaped}`
Balance SOL: {creator_balance_sol:.5f}
Transactions: {creator_tx_count}

{dev_wallet_empty_tag_escaped}
{wallet_history_tag_escaped}

📡 Telegram Link: {telegram_link_escaped}
X Link: {twitter_link_escaped}
    """
    return message

# Health check handler
async def health_check_handler(request):
    """Simple health check endpoint."""
    return web.json_response({"status": "healthy"})

async def start_health_server():
    """Starts a lightweight HTTP server for health checks."""
    app = web.Application()
    app.router.add_get('/healthz', health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HEALTH_CHECK_PORT)
    await site.start()
    logging.info(f"Health check server started on port {HEALTH_CHECK_PORT}")

async def main_loop():
    global metadata_semaphore # Ensure semaphore is accessible globally
    
    # Check for all critical environment variables
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, SOLANA_RPC_URL, COMMERCIAL_METADATA_API_URL, COMMERCIAL_API_KEY]):
        logging.error("Missing critical environment variables. Please check your .env file for TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, SOLANA_RPC_URL, COMMERCIAL_METADATA_API_URL, and COMMERCIAL_API_KEY.")
        return

    os.makedirs('data', exist_ok=True)
    processed_tokens = load_processed_tokens()
    
    # Initialize the semaphore
    metadata_semaphore = asyncio.Semaphore(CONCURRENT_METADATA_FETCHES) 

    logging.info(f"Loaded {len(processed_tokens)} previously processed tokens.")
    logging.info(f"Configured for {CONCURRENT_METADATA_FETCHES} concurrent metadata fetches.")

    # Start the health server as a concurrent task if enabled
    if ENABLE_HEALTH_CHECK:
        asyncio.create_task(start_health_server())

    while True:
        logging.info("Fetching new tokens from pump.fun...")
        all_tokens = await fetch_new_pump_fun_tokens()
        valid_tokens = [token for token in all_tokens if token.get("mint") and token.get("creator")]
        
        newly_found_tokens = []
        for token in valid_tokens:
            mint_address = token.get("mint")
            if mint_address and mint_address not in processed_tokens:
                newly_found_tokens.append(token)
                
        if not newly_found_tokens:
            logging.info("No new tokens found this cycle.")
        else:
            logging.info(f"Found {len(newly_found_tokens)} new tokens.")

        for token in newly_found_tokens:
            mint_address = token.get("mint")
            name = token.get("name", "N/A")
            symbol = token.get("symbol", "N/A")
            creator_address = token.get("creator")

            logging.info(f"Processing new token: {name} (${symbol}) - {mint_address}")

            # --- Fetch enriched metadata including social links ---
            enriched_metadata = await get_token_metadata_enriched(mint_address)
            
            telegram_link = "Not found"
            twitter_link = "Not found"

            if enriched_metadata:
                telegram_link = enriched_metadata.get("telegram_url") or enriched_metadata.get("telegram") or "Not found"
                twitter_link = enriched_metadata.get("twitter_url") or enriched_metadata.get("twitter") or "Not found"
                if "x.com" in twitter_link:
                    twitter_link = twitter_link.replace("x.com", "twitter.com")

            # --- Filter: Only post tokens with VALID Telegram and X links ---
            if not (telegram_link != "Not found" and is_valid_url(telegram_link) and \
                    twitter_link != "Not found" and is_valid_url(twitter_link)):
                logging.info(f"Skipping {name} (${symbol}) - {mint_address} due to missing or invalid Telegram/X links.")
                processed_tokens.add(mint_address) # Mark as processed even if skipped
                continue 

            creator_balance_sol = None
            creator_tx_count = None

            if creator_address:
                try:
                    creator_balance_sol = await get_sol_balance(creator_address, SOLANA_RPC_URL)
                    creator_tx_count = await get_transaction_count(creator_address, SOLANA_RPC_URL)
                except Exception as e:
                    logging.warning(f"Could not fetch creator info for {creator_address} ({name} ${symbol}): {e}")
            else:
                logging.warning(f"No creator address found for token: {name} (${symbol}) - {mint_address}")

            formatted_message = format_token_message(token, creator_balance_sol, creator_tx_count, telegram_link, twitter_link)
            success = await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, formatted_message)

            if success:
                logging.info(f"Posted new token: {name} (${symbol}) - {mint_address} with social links.")
                processed_tokens.add(mint_address) # Mark as processed only if successfully posted
            else:
                logging.error(f"Failed to post token: {name} (${symbol}) - {mint_address}. Will retry next cycle.")
                # Do NOT add to processed_tokens if failed to post, so it can be retried.

        # Save processed tokens after the current batch is handled
        save_processed_tokens(processed_tokens)
        
        logging.info(f"Processed tokens updated. Current count: {len(processed_tokens)}")
        logging.info(f"Waiting for {POLLING_INTERVAL_SECONDS} seconds before next check...")
        await asyncio.sleep(POLLING_INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(main_loop())
