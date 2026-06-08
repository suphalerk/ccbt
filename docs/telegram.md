# Telegram Integration

## Alerts (bot/telegram.py)
Automatic notifications sent to Telegram on critical events:

| Event | Emoji | When |
|-------|-------|------|
| CCBT Started | 🟢 | Process start (1 summary, not per-bot) |
| Trade opened | 📈 | Entry/SL/TP/size details |
| Trade closed (profit) | ✅ | PnL $, %, duration, close reason |
| Trade closed (loss) | ❌ | PnL $, %, duration, close reason |
| PANIC mode | 🚨 | Emergency close all |
| SL verify failed | ⚠️ | Emergency close |
| Bot halted | 🛑 | API error threshold exceeded |
| CCBT Stopped | 🔴 | Process stop (1 summary) |

## Commands (bot/telegram_commands.py)
Interactive commands via Telegram chat — send to the bot:

| Command | Description |
|---------|-------------|
| `/status` | Running bots count, uptime, RAM |
| `/balance` | Current USDT balance |
| `/pnl` | Today + total PnL, win rate |
| `/positions` | Open positions with entry/side/size |
| `/upnl` | **Live unrealized PnL** per position + total (queries the exchange directly via a dedicated thread-isolated ccxt clone — realtime, not candle-paced; degrades gracefully if exchange unconnected; HTML in errors is escaped) |
| `/bots` | All bots listed by status |
| `/mode` | Mode distribution across bots |
| `/panic` | Set ALL bots to PANIC |
| `/stop` | Set ALL bots to GRACEFUL_STOP |
| `/resume` | Set ALL bots back to NORMAL |
| `/help` | List all commands |

## Setup
```bash
# .env
TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
TELEGRAM_CHAT_ID=<your chat ID>
```
