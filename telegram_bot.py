# utils/telegram_bot.py
import httpx
import logging

# Configure logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def send_telegram_message(bot_token: str, chat_id: str, message: str) -> bool:
    """
    Sends a message to a specified Telegram chat using MarkdownV2.
    Returns True on success, False otherwise.
    """
    if not bot_token or not chat_id:
        logger.error("Telegram bot token or chat ID is missing. Cannot send message.")
        return False

    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True # Optional: disable link previews to keep messages clean
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(telegram_api_url, json=payload, timeout=10.0)
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            response_json = response.json()
            if response_json.get("ok"):
                logger.info("Message sent to Telegram successfully.")
                return True
            else:
                logger.error(f"Failed to send Telegram message. Response: {response_json}")
                return False
    except httpx.TimeoutException:
        logger.error("Timeout when sending message to Telegram.")
    except httpx.RequestError as e:
        logger.error(f"Error sending message to Telegram: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending Telegram message: {e}")
    return False
