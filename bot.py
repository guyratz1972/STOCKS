import os
import logging
from datetime import datetime, time
import asyncio
import pytz
import yfinance as yf
import anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

TICKERS = {
    "SPY":  {"name": "S&P 500",           "stops": {"warn": 641,  "soft": 608,  "hard": 555},  "targets": [727,  775,  828]},
    "QQQ":  {"name": "Nasdaq 100",        "stops": {"warn": 576,  "soft": 547,  "hard": 500},  "targets": [647,  688,  737]},
    "IWM":  {"name": "Russell 2000",      "stops": {"warn": 246,  "soft": 233,  "hard": 213},  "targets": [275,  293,  313]},
    "MAGS": {"name": "Magnificent 7 ETF", "stops": {"warn": 61,   "soft": 57,   "hard": 52},   "targets": [68,   73,   78]},
    "XHB":  {"name": "Homebuilders ETF",  "stops": {"warn": 96,   "soft": 91,   "hard": 83},   "targets": [108,  115,  123]},
    "GRNY": {"name": "Granny Shots ETF",  "stops": {"warn": 23.5, "soft": 22.3, "hard": 20.3}, "targets": [26.3, 28.0, 30.0]},
    "ETHA": {"name": "Ethereum ETF",      "stops": {"warn": 15.5, "soft": 14.7, "hard": 13.4}, "targets": [17.4, 18.5, 19.8]},
    "NVDA": {"name": "Nvidia",            "stops": {"warn": 186,  "soft": 177,  "hard": 161},  "targets": [209,  222,  238]},
    "AVGO": {"name": "Broadcom",          "stops": {"warn": 369,  "soft": 350,  "hard": 320},  "targets": [413,  440,  471]},
    "TSLA": {"name": "Tesla",             "stops": {"warn": 369,  "soft": 351,  "hard": 320},  "targets": [413,  440,  471]},
    "PLTR": {"name": "Palantir",          "stops": {"warn": 132,  "soft": 125,  "hard": 114},  "targets": [148,  158,  169]},
    "PANW": {"name": "Palo Alto",         "stops": {"warn": 157,  "soft": 149,  "hard": 136},  "targets": [176,  187,  201]},
    "AMZN": {"name": "Amazon",            "stops": {"warn": 225,  "soft": 213,  "hard": 195},  "targets": [252,  268,  287]},
    "GOOG": {"name": "Alphabet",          "stops": {"warn": 316,  "soft": 300,  "hard": 274},  "targets": [354,  377,  403]},
    "BAC":  {"name": "Bank of America",   "stops": {"warn": 51,   "soft": 48,   "hard": 44},   "targets": [57,   61,   65]},
    "COIN": {"name": "Coinbase",          "stops": {"warn": 186,  "soft": 176,  "hard": 161},  "targets": [208,  221,  237]},
    "GLXY": {"name": "Galaxy Digital",    "stops": {"warn": 22.7, "soft": 21.5, "hard": 19.7}, "targets": [25.4, 27.1, 29.0]},
    "NYXH": {"name": "Nyxoah",            "stops": {"warn": 2.65, "soft": 2.40, "hard": 2.10}, "targets": [3.50, 4.50, 6.00]},
}

# Track last sent times to avoid duplicates
last_sent = {"open": None, "close": None}


def get_status(price, ticker_data):
    stops = ticker_data["stops"]
    targets = ticker_data["targets"]
    if price <= stops["hard"]:
        return ("🔴", "Hard stop - exit now!")
    elif price <= stops["soft"]:
        return ("🟠", "Soft stop - consider exit")
    elif price <= stops["warn"]:
        return ("🟡", "Warning - watch closely")
    elif price >= targets[2]:
        return ("🎯", "Target 3 - full exit")
    elif price >= targets[1]:
        return ("🎯", "Target 2 - main exit")
    elif price >= targets[0]:
        return ("🎯", "Target 1 - partial exit")
    else:
        return ("✅", "In range - hold")


def fetch_prices():
    prices = {}
    for sym in TICKERS:
        try:
            price = round(float(yf.Ticker(sym).fast_info.last_price), 2)
            prices[sym] = price
        except Exception as e:
            logger.error(f"Error fetching {sym}: {e}")
            prices[sym] = None
    return prices


def pct(current, ref):
    if ref == 0:
        return "N/A"
    p = (current - ref) / ref * 100
    return f"{'+' if p > 0 else ''}{p:.1f}%"


def build_message(session_type):
    now = datetime.now(ISRAEL_TZ)
    prices = fetch_prices()
    lines = []
    alerts = []
    summary = []

    for sym, data in TICKERS.items():
        price = prices.get(sym)
        if price is None:
            lines.append(f"⚠️ *{sym}* — not available")
            continue
        emoji, status = get_status(price, data)
        stops = data["stops"]
        targets = data["targets"]
        lines.append(
            f"{emoji} *{sym}* — ${price:,.2f}\n"
            f"   {status}\n"
            f"   🟡 Warn: ${stops['warn']} ({pct(price, stops['warn'])}) | 🎯 T1: ${targets[0]} ({pct(price, targets[0])})"
        )
        if emoji in ("🔴", "🟠"):
            alerts.append(f"⚠️ {sym}: {status}")
        summary.append(f"{sym}: ${price} - {status}")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system="You are an experienced trader. Give short accurate analysis in Hebrew. No legal disclaimers.",
            messages=[{"role": "user", "content": f"Session: {session_type}\nPortfolio:\n" + "\n".join(summary) + "\n\nWrite 2-3 short sentences in Hebrew: overall picture + one concrete recommendation."}]
        )
        ai_text = resp.content[0].text
    except Exception as e:
        ai_text = f"(AI unavailable: {e})"

    alert_block = ("\n🚨 *ALERTS:*\n" + "\n".join(alerts) + "\n") if alerts else ""

    return (
        f"📊 *Market Report - {session_type}*\n"
        f"🕐 {now.strftime('%H:%M')} | 📅 {now.strftime('%d/%m/%Y')}\n"
        f"{'─' * 28}\n\n"
        + "\n\n".join(lines) +
        f"\n\n{'─' * 28}\n"
        f"{alert_block}"
        f"\n💡 *Analysis:*\n{ai_text}\n\n"
        f"_Not financial advice_"
    )


async def send_report(bot, session_type):
    logger.info(f"Sending report: {session_type}")
    msg = build_message(session_type)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    logger.info("Report sent.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        await update.message.reply_text("Unauthorized.")
        return
    text = update.message.text.strip().lower() if update.message and update.message.text else ""
    if text == "run":
        await update.message.reply_text("Running report...")
        await send_report(context.bot, "Manual Run")
    elif text == "help":
        await update.message.reply_text("Commands:\nrun - Run report now\nhelp - Show commands")
    else:
        await update.message.reply_text("Type 'run' for a report.")


async def scheduler_loop(bot):
    logger.info("Scheduler loop started.")
    while True:
        now = datetime.now(ISRAEL_TZ)
        h = now.hour
        m = now.minute
        weekday = now.weekday()  # 0=Mon, 6=Sun
        # Mon-Fri = 0-4, but Israel week: Sun-Thu
        # In Israel: Sun=6, Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5
        is_trading_day = weekday in (0, 1, 2, 3, 6)  # Mon-Thu + Sun

        today_str = now.strftime("%Y-%m-%d")

        if is_trading_day and h == 16 and m == 30:
            if last_sent.get("open") != today_str:
                last_sent["open"] = today_str
                await send_report(bot, "Market Open")

        if is_trading_day and h == 22 and m == 0:
            if last_sent.get("close") != today_str:
                last_sent["close"] = today_str
                await send_report(bot, "1hr Before Close")

        await asyncio.sleep(30)


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.info("Bot started. Running scheduler loop.")
    await scheduler_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())

