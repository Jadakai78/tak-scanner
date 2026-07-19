# TAK-Scanner S/A Alert Setup Guide

## Overview
This guide will help you configure **redundant alerts** for S and A grade signals via **Telegram** and **Outlook** email, ensuring you never miss a high-priority trading signal even if one delivery channel fails.

## What You'll Get
- ✅ **Dual-channel delivery**: Every S/A signal sent to BOTH Telegram and Outlook
- ✅ **Retry logic**: 3 automatic retries per channel
- ✅ **Failover redundancy**: If one channel fails, the other still delivers
- ✅ **Priority alerts**: High-priority email flags and instant Telegram push

---

## Step 1: Set Up Telegram Bot

### 1.1 Create a Bot
1. Open Telegram and message **@BotFather**
2. Send `/newbot`
3. Follow prompts to name your bot (e.g., "TAK Scanner Alerts")
4. Copy the **bot token** (looks like `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 1.2 Get Your Chat ID
1. Message your new bot (send any message like "/start")
2. Open this URL in your browser:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   Replace `<YOUR_BOT_TOKEN>` with the token from step 1.1
3. Look for `"chat":{"id":123456789}` in the response - that's your **Chat ID**

### 1.3 Add to .env File
Add these lines to your `.env` file:
```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

---

## Step 2: Set Up Outlook Email Alerts

### 2.1 Create an App Password (Required for SMTP)
1. Go to [Microsoft Account Security](https://account.microsoft.com/security)
2. Sign in with your Outlook account
3. Navigate to **Advanced security options** → **App passwords**
4. Click "Create a new app password"
5. Copy the generated password (looks like `abcd-efgh-ijkl-mnop`)

### 2.2 Add to .env File
Add these lines to your `.env` file:
```bash
OUTLOOK_EMAIL=your_email@outlook.com
OUTLOOK_PASSWORD=abcd-efgh-ijkl-mnop
ALERT_RECIPIENT=your_email@outlook.com  # Can be different if you want
```

---

## Step 3: Test the Alert Router

Run the alert router manually to verify setup:
```bash
python3 alert_router_sa.py
```

### Expected Output (No Signals)
```
Starting S/A Alert Router...
No S or A grade signals to push
```

### Expected Output (With S/A Signals)
```
Starting S/A Alert Router...
Found 2 S/A grade signals to push
✅ Telegram delivered (attempt 1)
✅ Outlook email delivered (attempt 1)
✅ BTC/USD [S] delivered via BOTH channels
...
```

---

## Step 4: Integrate with Your Scanner

### Option A: Run Manually After Scans
Add to your workflow:
```bash
python3 tak_scanner_v4.py
python3 alert_router_sa.py
```

### Option B: Schedule with Cron (Linux/Mac)
Edit crontab:
```bash
crontab -e
```

Add this line to run every 30 minutes:
```bash
*/30 * * * * cd /path/to/tak-scanner && python3 alert_router_sa.py >> /var/log/alerts.log 2>&1
```

### Option C: Windows Task Scheduler
1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (e.g., every 30 minutes)
4. Set action: Run `python3 alert_router_sa.py`

---

## Step 5: Verify Alerts Are Working

### Test with Sample Signal
1. Add a test S-grade signal to `signal_bus.json`:
```json
{
  "signals": [
    {
      "pair": "BTC/USD",
      "grade": "S",
      "signal_name": "TEST SIGNAL",
      "score": 0.95,
      "trend_context": "BULLISH"
    }
  ]
}
```

2. Run the alert router:
```bash
python3 alert_router_sa.py
```

3. Check:
   - 📱 **Telegram**: You should get a message with signal details
   - 📧 **Outlook**: Check your inbox for an email with subject `🚨 [S] BTC/USD - TEST SIGNAL`

---

## Troubleshooting

### Telegram Not Working
- Verify bot token is correct in `.env`
- Ensure you've sent a message to the bot first
- Check Chat ID is a number (not a string)
- Test bot token manually:
  ```bash
  curl https://api.telegram.org/bot<TOKEN>/getMe
  ```

### Outlook Not Working
- Verify you're using an **app password**, not your regular password
- Check email address is correct
- Ensure 2FA is enabled on your Microsoft account (required for app passwords)
- Test SMTP manually:
  ```bash
  telnet smtp-mail.outlook.com 587
  ```

### Signals Not Being Picked Up
- Verify `signal_bus.json` exists and has valid JSON
- Check that signals have `grade` field set to "S" or "A"
- Ensure the script can read the file (check permissions)

---

## Log Monitoring

Check logs to see delivery status:
```bash
tail -f /var/log/alerts.log  # Linux/Mac
type alerts.log              # Windows
```

Look for:
- `✅` = Successful delivery
- `⚠️` = Partial delivery (one channel failed)
- `❌` = Complete failure (both channels failed)

---

## Security Best Practices

1. **Never commit `.env` to git**
   - Already in `.gitignore`
   - Contains sensitive tokens

2. **Use app passwords only**
   - Never use your main Outlook password
   - App passwords can be revoked separately

3. **Limit bot access**
   - Don't share your bot token
   - Only you should message the bot

4. **Monitor alerts**
   - Set up a weekly test to ensure system is working
   - Check logs regularly

---

## Next Steps

1. Integrate with scheduler for automatic runs
2. Set up log rotation to prevent disk fill
3. Consider adding SMS backup via Twilio for ultra-critical alerts
4. Monitor delivery success rate over time

---

**Questions? Issues?**
Check the logs first, then review the error messages. Most issues are due to:
- Incorrect credentials in `.env`
- Bot not initiated (send `/start` to your bot)
- Firewall blocking SMTP port 587
