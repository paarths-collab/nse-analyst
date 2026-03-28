"""
Telegram Alert Notifier for NSE Filing Analysis.

Usage:
    - Set env vars: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    - Call send_telegram_alert / send_detailed_telegram_alert
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from infra.config import load_env_file as load_root_env_file

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Maintain one process-wide event loop for Telegram async calls.
_event_loop: asyncio.AbstractEventLoop | None = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _event_loop

    if _event_loop is None or _event_loop.is_closed():
        try:
            _event_loop = asyncio.get_event_loop()
            if _event_loop.is_closed():
                _event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_event_loop)
        except RuntimeError:
            _event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_event_loop)

    return _event_loop


def _run_coro(coro):
    loop = _get_event_loop()
    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


def _load_env_file(path: Path) -> None:
    try:
        if not path.exists():
            return

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def _load_candidate_env_files() -> None:
    # Primary path for this repo.
    load_root_env_file()

    # Extra fallbacks keep this module usable when run directly.
    this_file = Path(__file__).resolve()
    candidates = [
        Path.cwd() / ".env",
        this_file.parent / ".env",
        this_file.parents[1] / ".env",
        this_file.parents[2] / ".env",
    ]
    for candidate in candidates:
        _load_env_file(candidate)


def _get_telegram_config() -> tuple[str, str]:
    _load_candidate_env_files()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def send_telegram_alert(
    symbol: str,
    verdict: str,
    price_move: float,
    time_horizon: str,
    reasoning_short: str,
    catalysts: list,
    risks: list,
) -> bool:
    """Send a concise Telegram alert."""

    telegram_bot_token, telegram_chat_id = _get_telegram_config()
    if not telegram_bot_token or not telegram_chat_id:
        logger.warning("Telegram disabled: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    emoji = "📈" if price_move > 0 else "📉" if price_move < 0 else "➡️"
    price_str = f"{price_move:+.1f}%"
    verdict_emoji = (
        "🟢"
        if verdict == "BULLISH"
        else "🔴"
        if verdict == "BEARISH"
        else "⚪"
        if verdict == "NEUTRAL"
        else "🟡"
    )

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

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            bot = Bot(token=telegram_bot_token)
            _run_coro(
                bot.send_message(
                    chat_id=telegram_chat_id,
                    text=message,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            )
            logger.info("Telegram alert sent for %s (attempt %d)", symbol, attempt)
            return True
        except TelegramError as e:
            logger.error("Telegram error for %s (attempt %d/%d): %s", symbol, attempt, max_attempts, e)
            if attempt < max_attempts:
                time.sleep(2**attempt)
        except Exception as e:
            logger.error("Unexpected Telegram error for %s (attempt %d/%d): %s", symbol, attempt, max_attempts, e)
            if attempt < max_attempts:
                time.sleep(2**attempt)

    logger.error("Failed to send Telegram alert for %s after %d attempts", symbol, max_attempts)
    return False


def send_detailed_telegram_alert(
    symbol: str,
    verdict: str,
    price_move: float,
    time_horizon: str,
    reasoning_short: str,
    reasoning_long: str,
    catalysts: list,
    risks: list,
    sources: list,
) -> bool:
    """Send a detailed Telegram alert in two messages."""

    telegram_bot_token, telegram_chat_id = _get_telegram_config()
    if not telegram_bot_token or not telegram_chat_id:
        logger.warning("Telegram disabled: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    emoji = "📈" if price_move > 0 else "📉" if price_move < 0 else "➡️"
    price_str = f"{price_move:+.1f}%"
    verdict_emoji = (
        "🟢"
        if verdict == "BULLISH"
        else "🔴"
        if verdict == "BEARISH"
        else "⚪"
        if verdict == "NEUTRAL"
        else "🟡"
    )

    msg1 = f"""{verdict_emoji} {verdict} | {emoji} {price_str}

{symbol} - {time_horizon}

Quick Take:
{reasoning_short}
"""

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

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            bot = Bot(token=telegram_bot_token)
            _run_coro(
                bot.send_message(
                    chat_id=telegram_chat_id,
                    text=msg1,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            )
            _run_coro(
                bot.send_message(
                    chat_id=telegram_chat_id,
                    text=msg2,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            )
            logger.info("Detailed Telegram alerts sent for %s (attempt %d)", symbol, attempt)
            return True
        except TelegramError as e:
            logger.error("Telegram error for %s (attempt %d/%d): %s", symbol, attempt, max_attempts, e)
            if attempt < max_attempts:
                time.sleep(2**attempt)
        except Exception as e:
            logger.error("Unexpected Telegram error for %s (attempt %d/%d): %s", symbol, attempt, max_attempts, e)
            if attempt < max_attempts:
                time.sleep(2**attempt)

    logger.error("Failed to send detailed Telegram alert for %s after %d attempts", symbol, max_attempts)
    return False


if __name__ == "__main__":
    ok = send_telegram_alert(
        symbol="TEST",
        verdict="BULLISH",
        price_move=2.5,
        time_horizon="MEDIUM_TERM",
        reasoning_short="This is a test message.",
        catalysts=["Catalyst 1", "Catalyst 2"],
        risks=["Risk 1", "Risk 2"],
    )
    print("\nTelegram notification working!" if ok else "\nTelegram notification failed. Check env vars.")
