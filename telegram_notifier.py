"""
Telegram Alert Notifier for NSE Filing Analysis

Usage:
    - Set env vars: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    - Call send_alert(symbol, verdict, price_move, reasoning_short) in fillings.py
"""

import os
import asyncio
import logging
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# CRITICAL: Maintain a single event loop across the lifetime of the process
# to avoid "Event loop is closed" errors when sending multiple Telegram messages
_event_loop: asyncio.AbstractEventLoop | None = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create the global event loop for Telegram async operations."""
    global _event_loop
    
    if _event_loop is None or _event_loop.is_closed():
        try:
            # Try to get the current event loop
            _event_loop = asyncio.get_event_loop()
            if _event_loop.is_closed():
                _event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_event_loop)
        except RuntimeError:
            # No current event loop, create one
            _event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_event_loop)
    
    return _event_loop


def _run_coro(coro):
    """
    FIXED: Run coroutine safely using a persistent event loop.
    This prevents "Event loop is closed" errors on subsequent calls.
    """
    loop = _get_event_loop()
    
    if loop.is_running():
        # Rare case: loop is already running (nested call)
        # Create a new task and wait
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        # Normal case: use existing loop
        return loop.run_until_complete(coro)


def load_env_file(env_path: str = ".env"):
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(base_dir, env_path)
        if not os.path.exists(full_path):
            return

        with open(full_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and not os.environ.get(key):
                    os.environ[key] = value
    except Exception:
        pass


load_env_file(".env")


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram_alert(symbol: str, verdict: str, price_move: float, 
                       time_horizon: str, reasoning_short: str, 
                       catalysts: list, risks: list) -> bool:
    """
    Send a concise alert via Telegram with retry logic.
    
    Args:
        symbol: Stock symbol (e.g., "INFY")
        verdict: BULLISH | BEARISH | NEUTRAL | WATCH
        price_move: Expected price change percent (e.g., 2.5 or -1.2)
        time_horizon: SHORT_TERM | MEDIUM_TERM | LONG_TERM
        reasoning_short: 3-5 sentence executive summary
        catalysts: List of positive catalysts
        risks: List of downside risks
    
    Returns:
        True if sent successfully, False otherwise
    """
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(f"Telegram disabled: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    # Format price move with emoji
    emoji = "📈" if price_move > 0 else "📉" if price_move < 0 else "➡️"
    price_str = f"{price_move:+.1f}%"
    
    # Format verdict with emoji
    verdict_emoji = "🟢" if verdict == "BULLISH" else "🔴" if verdict == "BEARISH" else "⚪" if verdict == "NEUTRAL" else "🟡"
    
    # Build message as plain text (no markdown parsing).
    message = f"""{verdict_emoji} {verdict} | {emoji} {price_str}

{symbol} - {time_horizon}

Quick Take:
{reasoning_short}

Catalysts:
"""
    for cat in catalysts[:3]:
        message += f"• {cat}\n"
    
    message += "\nRisks:\n"
    for risk in risks[:3]:
        message += f"• {risk}\n"
    
    # Send with retry logic
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            
            _run_coro(
                bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=message,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            )
            
            logger.info(f"Telegram alert sent for {symbol} (attempt {attempt})")
            return True
        
        except TelegramError as e:
            logger.error(f"Telegram error for {symbol} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                asyncio.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s, 8s
        except Exception as e:
            logger.error(f"Unexpected Telegram error for {symbol} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                asyncio.sleep(2 ** attempt)
    
    logger.error(f"Failed to send Telegram alert for {symbol} after {max_attempts} attempts")
    return False


def send_detailed_telegram_alert(symbol: str, verdict: str, price_move: float,
                                time_horizon: str, reasoning_short: str,
                                reasoning_long: str, catalysts: list, 
                                risks: list, sources: list) -> bool:
    """
    Send a detailed alert via Telegram (2-part message for character limit).
    Includes retry logic with exponential backoff.
    
    Args:
        symbol: Stock symbol
        verdict: BULLISH | BEARISH | NEUTRAL | WATCH
        price_move: Expected price change %
        time_horizon: SHORT_TERM | MEDIUM_TERM | LONG_TERM
        reasoning_short: Executive summary
        reasoning_long: Detailed institutional thesis
        catalysts: List of positive catalysts
        risks: List of downside risks
        sources: List of news source URLs
    
    Returns:
        True if both messages sent successfully
    """
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(f"Telegram disabled: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    # Emoji formatting
    emoji = "📈" if price_move > 0 else "📉" if price_move < 0 else "➡️"
    price_str = f"{price_move:+.1f}%"
    verdict_emoji = "🟢" if verdict == "BULLISH" else "🔴" if verdict == "BEARISH" else "⚪" if verdict == "NEUTRAL" else "🟡"
    
    # PART 1: Header + Quick Take (plain text)
    msg1 = f"""{verdict_emoji} {verdict} | {emoji} {price_str}

{symbol} - {time_horizon}

Quick Take:
{reasoning_short}
"""
    
    # PART 2: Detailed Analysis + Catalysts + Risks + Sources
    msg2 = f"""Detailed Analysis:
{reasoning_long}

Catalysts:
"""
    for cat in catalysts[:3]:
        msg2 += f"• {cat}\n"
    
    msg2 += "\nRisks:\n"
    for risk in risks[:3]:
        msg2 += f"• {risk}\n"
    
    if sources:
        msg2 += "\nNews Sources:\n"
        for src in sources[:3]:
            msg2 += f"🔗 {src}\n"
    
    # Send both messages with retry logic
    max_attempts = 3
    
    for attempt in range(1, max_attempts + 1):
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            
            # Send message 1
            _run_coro(
                bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg1,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            )
            
            # Send message 2
            _run_coro(
                bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg2,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            )
            
            logger.info(f"Detailed Telegram alerts sent for {symbol} (attempt {attempt})")
            return True
        
        except TelegramError as e:
            logger.error(f"Telegram error for {symbol} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s, 8s
        except Exception as e:
            logger.error(f"Unexpected Telegram error for {symbol} (attempt {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                import time
                time.sleep(2 ** attempt)
    
    logger.error(f"Failed to send detailed Telegram alert for {symbol} after {max_attempts} attempts")
    return False


if __name__ == "__main__":
    # Test function
    test_result = send_telegram_alert(
        symbol="TEST",
        verdict="BULLISH",
        price_move=2.5,
        time_horizon="MEDIUM_TERM",
        reasoning_short="This is a test message.",
        catalysts=["Catalyst 1", "Catalyst 2"],
        risks=["Risk 1", "Risk 2"]
    )
    
    if test_result:
        print("\n✓ Telegram notification working!")
    else:
        print("\n✗ Telegram notification failed. Check env vars.")
