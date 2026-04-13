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

TICKERS = {
    "AMZN":  {"name": "Amazon",       "stops": {"warn": 220, "soft": 207, "hard": 190}, "targets": [248, 257, 268]},
    "GOOG":  {"name": "Alphabet",     "stops": {"warn": 295, "soft": 279, "hard": 253}, "targets": [328, 342, 350]},
    "AVGO":  {"name": "Broadcom",     "stops": {"warn": 345, "soft": 322, "hard": 297}, "targets": [388, 403, 415]},
    "IWM":   {"name": "Russell 2000", "stops": {"warn": 245, "soft": 232, "hard": 209}, "targets": [267, 272, 282]},
    "QQQ":   {"name": "Nasdaq 100",   "stops": {"warn": 575, "soft": 550, "hard": 490}, "targets": [621, 633, 640]},
    "SPY":   {"name": "S&P 500",      "stops": {"warn": 640, "soft": 612, "hard": 544}, "targets": [689, 697, 710]},
    "ETHA":  {"name": "Ethereum ETF", "stops": {"warn": 14.5,"soft": 13.0,"hard": 11.5}, "targets": [18.5, 23.0, 29.0]},
}


def get_status(price, ticker_data):
    stops = ticker_data["stops"]
    targets = ticker_data["targets"]
    if price <= stops["hard"]:
        return ("🔴", "סטופ קשה — יציאה מיידית!")
    elif price <= stops["soft"]:
        return ("🟠", "סטופ רך — שקול יציאה")
    elif price <= stops["warn"]:
        return ("🟡", "אזהרה — עקוב מקרוב")
    elif price >= targets[2]:
        return ("🎯", "יעד 3 — יציאה מלאה")
    elif price >= targets[1]:
        return ("🎯", "יעד 2 — יציאה עיקרית")
    elif price >= targets[0]:
        return ("🎯", "יעד 1 — יציאה חלקית")
    else:
        return ("✅", "בטווח — המשך להחזיק")

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
            lines.append(f"⚠️ *{sym}* — לא זמין")
            continue

        emoji, status_text = get_status(price, data)
        stops = data["stops"]
        targets = data["targets"]

        dist_hard = pct(price, stops["hard"])
        dist_t1 = pct(price, targets[0])

        line = (
            f"{emoji} *{sym}* — ${price:,.2f}\n"
            f"   ↳ {status_text}\n"
            f"   🔴 קשה: ${stops['hard']} ({dist_hard}) | 🎯 יעד1: ${targets[0]} ({dist_t1})"
        )
        lines.append(line)

        if emoji in ("🔴", "🟠"):
            alerts.append(f"⚠️ {sym}: {status_text}")

        summary_data.append(f"{sym}: ${price} → {status_text}")

    return "\n\n".join(lines), alerts, summary_data


def get_ai_summary(summary_data, session_type):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        f"סשן: {session_type}\n"
        f"מצב הניירות:\n" + "\n".join(summary_data) +
        "\n\nכתוב 2-3 משפטים קצרים בעברית: תמונה כוללת + המלצה אחת קונקרטית. ישיר ותמציתי."
    )
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system="אתה סוחר מנוסה. תן ניתוח קצר ומדויק. אל תכלול הסתייגויות משפטיות.",
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


async def send_report(session_type: str):
    bot = Bot(token=TELEGRAM_TOKEN)
    now = datetime.now(ISRAEL_TZ)
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%d/%m/%Y")

    logger.info(f"Running market check — {session_type}")

    prices = fetch_prices()
    ticker_lines, alerts, summary_data = build_ticker_lines(prices)

    try:
        ai_summary = get_ai_summary(summary_data, session_type)
    except Exception as e:
        ai_summary = f"(ניתוח AI לא זמין: {e})"

    alert_block = ""
    if alerts:
        alert_block = "\n🚨 *התראות דחופות:*\n" + "\n".join(alerts) + "\n"

    message = (
        f"📊 *דוח שוק — {session_type}*\n"
        f"🕐 {time_str} | 📅 {date_str}\n"
        f"{'─' * 30}\n\n"
        f"{ticker_lines}\n\n"
        f"{'─' * 30}\n"
        f"{alert_block}"
        f"\n💡 *ניתוח:*\n{ai_summary}\n\n"
        f"_* אינו ייעוץ פיננסי_"
    )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info("Report sent successfully")


async def main():
    scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

    # פתיחת שוק — 16:30 שני עד שישי
    scheduler.add_job(
        send_report,
        "cron",
        day_of_week="mon-fri",
        hour=16, minute=30,
        args=["🔔 פתיחת שוק"]
    )

    # שעה לפני סגירה — 22:00 שני עד שישי
    scheduler.add_job(
        send_report,
        "cron",
        day_of_week="mon-fri",
        hour=22, minute=0,
        args=["⏰ שעה לפני סגירה"]
    )

    scheduler.start()
    logger.info("Bot scheduler started. Waiting for scheduled times...")

    # Keep alive
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
