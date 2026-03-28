# Telegram Notifications Setup

Get instant NSE filing alerts delivered to your Telegram chat.

---

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Start the chat and send:
   ```
   /newbot
   ```
3. Choose a **name** for your bot (e.g., "NSE Analyst Bot")
4. Choose a **username** (must end with `bot`, e.g., "nse_analyst_bot")
5. **Copy the token** that BotFather provides
   - Format: `123456789:ABCdefGHIjklmnoPQRstuvWXYZabcd`
   - **Keep this private** — anyone with this token can control your bot

---

## Step 2: Get Your Chat ID (Official Method)

Use Telegram's Bot API directly (official).

1. Start your newly created bot (search for the username you chose)
2. Send any message (e.g., "hello")
3. Open this URL in your browser:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   Replace `<YOUR_BOT_TOKEN>` with your actual token
4. Look for the JSON response and find `"chat":{"id":-123456789}`
5. **Copy that chat ID**

---

## Step 3: Set Environment Variables

### PowerShell (Windows):

```powershell
$env:TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklmnoPQRstuvWXYZabcd"
$env:TELEGRAM_CHAT_ID = "-123456789"
```

Then run:
```powershell
python .\fillings.py
```

### Bash (Linux/Mac):

```bash
export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklmnoPQRstuvWXYZabcd"
export TELEGRAM_CHAT_ID="-123456789"
python fillings.py
```

### Persistent (Save in PowerShell Profile):

**Edit your PowerShell profile:**
```powershell
$PROFILE
```

Add these lines:
```powershell
$env:GROQ_API_KEY = "gsk_..."
$env:TELEGRAM_BOT_TOKEN = "123456789:ABC..."
$env:TELEGRAM_CHAT_ID = "-123456789"
```

Save and reload PowerShell.

---

## Step 4: Test the Connection

### Option A: Test via Python

```powershell
python telegram_notifier.py
```

If successful:
```
✓ Telegram notification working!
```

If failed:
```
✗ Telegram notification failed. Check env vars.
WARN Telegram: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID
```

### Option B: Test via Script

Create `test_telegram.py`:
```python
from telegram_notifier import send_telegram_alert

send_telegram_alert(
    symbol="TEST",
    verdict="BULLISH",
    price_move=2.5,
    time_horizon="MEDIUM_TERM",
    reasoning_short="Test message from NSE Analyst Bot.",
    catalysts=["Test catalyst 1", "Test catalyst 2"],
    risks=["Test risk 1", "Test risk 2"]
)
```

Run:
```powershell
python test_telegram.py
```

You should receive a Telegram message immediately.

---

## Step 5: Enable Alerts in fillings.py

The script is **pre-configured** to send alerts if env vars are set. No code changes needed!

Two alert modes:
- **Light alerts**: Just verdict + price move + quick take
- **Full alerts**: Everything + detailed analysis + sources

Both are sent automatically if Telegram env vars are set.

---

## Alert Format

### Light Alert (Default)
```
🟢 BULLISH | 📈 +2.5%

INFY — MEDIUM_TERM

Quick Take:
Acquisition signals entry into $50B BFSI market. Historical 
precedent (TCS 2022) delivered +9.5% in 6 months. Moderate 
execution risk. Expect +2.5% medium-term.

Catalysts:
• High-margin BFSI services market entry
• Accretive to EPS within 12 months
• Strategic inorganic growth

Risks:
• Integration execution delays
• BFSI regulatory scrutiny
• Valuation compression risk
```

### Full Alert (Optional, 2-part message)
Part 1: Header + Quick Take  
Part 2: Detailed thesis + All catalysts + All risks + Sources

---

## Troubleshooting

### "Invalid token" Error
```
ERROR Telegram: Unauthorized (401)
```
→ Check your **TELEGRAM_BOT_TOKEN** is correct (no spaces, no quotes)

### "Chat not found" Error
```
ERROR Telegram: Bad Request (400) - Chat not found
```
→ Check your **TELEGRAM_CHAT_ID** is correct and negative (e.g., `-123456789`)

### "No such handler" Error
```
ERROR Telegram: (403) Forbidden - Bot blocked by user
```
→ You blocked the bot. Unblock it or create a new bot

### Env vars not working
**In PowerShell:**
```powershell
$env:TELEGRAM_BOT_TOKEN = "..."
python fillings.py  # must be same terminal session
```

**Across sessions:** Add to PowerShell profile (see Step 3)

### Test script doesn't import
```
ModuleNotFoundError: No module named 'telegram'
```
→ Install: `pip install python-telegram-bot`

---

## Disable/Enable Alerts

### Permanently Disable:
Just don't set the env vars. Script will skip alerts gracefully.

### Temporarily Disable:
Comment out the telegram import in fillings.py (not recommended — leave it enabled)

### Change Alert Verbosity:
Edit fillings.py process() function:
- Use `send_telegram_alert()` for light alerts
- Use `send_detailed_telegram_alert()` for full alerts

---

## Security Notes

⚠️ **NEVER share your TELEGRAM_BOT_TOKEN** — anyone with it can control your bot

✓ **Chat ID is safe to share** — it's just your user identifier

✓ **Tokens are read from env vars only** — not hardcoded in source

---

## Limits

- **Free tier**: Up to 30 messages/second (plenty for NSE alerts)
- **No message history**: Telegram stores 45 days of bot history
- **Text only**: This implementation sends markdown text (100 KB per message limit)

For large backtest reports, consider saving to JSON and retrieving manually from `pdfs/*.json`.

---

## Next Steps

1. Create your bot via @BotFather
2. Get your Chat ID using `getUpdates` from Telegram Bot API
3. Set env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
4. Run: `python telegram_notifier.py` to test
5. Run: `python fillings.py` to start receiving alerts

👉 You're now a high-stakes quantitative analyst with **real-time Telegram alerts**. 📊
