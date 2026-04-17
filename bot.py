import os
import asyncio
import logging
from datetime import datetime
import pytz
import yfinance as yf
import anthropic
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY")

TICKERS = {
    "SPY":  {"name": "S&P 500",           "stops": {"warn": 640,  "soft": 612,  "hard": 544},  "targets": [689, 697, 710]},
    "QQQ":  {"name": "Nasdaq 100",        "stops": {"warn": 575,  "soft": 550,  "hard": 490},  "targets": [621, 633, 640]},
    "IWM":  {"name": "Russell 2000",      "stops": {"warn": 245,  "soft": 232,  "hard": 209},  "targets": [267, 272, 282]},
    "MAGS": {"name": "Magnificent 7 ETF", "stops": {"warn": 60,   "soft": 55,   "hard": 48},   "targets": [68, 72, 78]},
    "XHB":  {"name": "Homebuilders ETF",  "stops": {"warn": 95,   "soft": 88,   "hard": 80},   "targets": [110, 118, 125]},
    "GRNY": {"name": "Granny Shots ETF",  "stops": {"warn": 23,   "soft": 21,   "hard": 19},   "targets": [26, 27.5, 29]},
    "ETHA": {"name": "Ethereum ETF",      "stops": {"warn": 14.5, "soft": 13.0, "hard": 11.5}, "targets": [18.5, 23.0, 29.0]},
    "NVDA": {"name": "Nvidia",            "stops": {"warn": 180,  "soft": 165,  "hard": 148},  "targets": [208, 215, 225]},
    "AVGO": {"name": "Broadcom",          "stops": {"warn": 345,  "soft": 322,  "hard": 297},  "targets": [388, 403, 415]},
    "TSLA": {"name": "Tesla",             "stops": {"warn": 360,  "soft": 335,  "hard": 300},  "targets": [420, 455, 490]},
    "PLTR": {"name": "Palantir",          "stops": {"warn": 128,  "soft": 118,  "hard": 105},  "targets": [155, 175, 195]},
    "PANW": {"name": "Palo Alto",         "stops": {"warn": 152,  "soft": 142,  "hard": 128},  "targets": [180, 200, 220]},
    "AMZN": {"name": "Amazon",            "stops": {"warn": 220,  "soft": 207,  "hard": 190},  "targets": [248, 257, 268]},
    "GOOG": {"name": "Alphabet",          "stops": {"warn": 295,  "soft": 279,  "hard": 253},  "targets": [328, 342, 350]},
    "BAC":  {"name": "Bank of America",   "stops": {"warn": 50,   "soft": 46,   "hard": 42},   "targets": [57, 61, 66]},
    "COIN": {"name": "Coinbase",          "stops": {"warn": 175,  "soft": 158,  "hard": 140},  "targets": [230, 280, 350]},
    "GLXY": {"name": "Galaxy Digital",    "stops": {"warn": 21,   "soft": 18,   "hard": 15},   "targets": [30, 38, 45]},
    "NYXH": {"name": "Nyxoah SA",         "stops": {"warn": 3.10, "soft": 2.80, "hard": 2.35}, "targets": [8.00, 10.25, 13.00]},
}

def get_status(price, ticker_data):
    stops = ticker_data["stops"]
    targets = ticker_data["targets"]
    if stops["hard"] == 0:
        return ("⚙️", "No levels set")
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
    symbols = list(TICKERS.keys())
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            data = ticker.fast_info
            price = round(float(data.last_price), 2)
            prices[sym] = price
        except Exception as e:
            logger.error(f"Error fetching {sym}: {e}")
            prices[sym] = None
    return prices

def pct(current, ref):
    if ref == 0:
        return "N/A"
    p = (current - ref) / ref * 100
    sign = "+" if p > 0 else ""
    return f"{sign}{p:.1f}%"

def build_ticker_lines(prices):
    lines = []
    alerts = []
    summary_data = []

    for sym, data in TICKERS.items():
        price = prices.get(sym)
        if price is None:
            lines.append(f"⚠️ *{sym}* — not available")
            continue

        emoji, status_text = get_status(price, data)
        stops = data["stops"]
        targets = data["targets"]

        dist_hard = pct(price, stops["hard"])
        dist_t1 = pct(price, targets[0])

        line = (
            f"{emoji} *{sym}* — ${price:,.2f}\n"
            f"   {status_text}\n"
            f"   🔴 Hard: ${stops['hard']} ({dist_hard}) | 🎯 T1: ${targets[0]} ({dist_t1})"
        )
        lines.append(line)

        if emoji in ("🔴", "🟠"):
            alerts.append(f"⚠️ {sym}: {status_text}")

        summary_data.append(f"{sym}: ${price} - {status_text}")

    return "\n\n".join(lines), alerts, summary_data

def get_ai_summary(summary_data, session_type):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        f"Session: {session_type}\n"
        f"Portfolio status:\n" + "\n".join(summary_data) +
        "\n\nWrite 2-3 short sentences in Hebrew: overall picture + one concrete recommendation. Direct and concise."
    )
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system="You are an experienced trader. Give short accurate analysis in Hebrew. No legal disclaimers.",
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

async def send_report(session_type: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    now = datetime.now(ISRAEL_TZ)
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%d/%m/%Y")

    logger.info(f"Running market check - {session_type}")

    prices = fetch_prices()
    ticker_lines, alerts, summary_data = build_ticker_lines(prices)

    try:
        ai_summary = get_ai_summary(summary_data, session_type)
    except Exception as e:
        ai_summary = f"(AI analysis unavailable: {e})"

    alert_block = ""
    if alerts:
        alert_block = "\n🚨 *ALERTS:*\n" + "\n".join(alerts) + "\n"

    message = (
        f"📊 *Market Report - {session_type}*\n"
        f"🕐 {time_str} | 📅 {date_str}\n"
        f"{'─' * 28}\n\n"
        f"{ticker_lines}\n\n"
        f"{'─' * 28}\n"
        f"{alert_block}"
        f"\n💡 *Analysis:*\n{ai_summary}\n\n"
        f"_Not financial advice_"
    )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info("Report sent successfully")

async def main():
    scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

    scheduler.add_job(
        send_report,
        "cron",
        day_of_week="mon-fri",
        hour=16, minute=30,
        args=["Market Open"]
    )

    scheduler.add_job(
        send_report,
        "cron",
        day_of_week="mon-fri",
        hour=22, minute=0,
        args=["1hr Before Close"]
    )

    scheduler.start()
    logger.info("Bot scheduler started. Waiting for scheduled times...")

    while True:
        await asyncio.sleep(60)

if __name__ == '__main__':
    asyncio.run(main())
