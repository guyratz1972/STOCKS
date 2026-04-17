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

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Levels updated: April 17, 2026
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
    "NYXA": {"name": "NYXA",              "stops": {"warn": 0,    "soft": 0,    "hard": 0},    "targets": [0,    0,    0]},
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

        dist_warn = pct(price, stops["warn"])
        dist_t1 = pct(price, targets[0])

        line = (
            f"{emoji} *{sym}* — ${price:,.2f}\n"
            f"   {status_text}\n"
            f"   🟡 Warn: ${stops['warn']} ({dist_warn}) | 🎯 T1: ${targets[0]} ({dist_t1})"
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


if __name__ == "__main__":
    asyncio.run(main())
