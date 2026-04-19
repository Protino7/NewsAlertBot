#!/usr/bin/env python3
"""
NewsAlertBot v1.0 - Alert Notizie Live per Fabio
Bot Telegram che monitora notizie impattanti in tempo reale da fonti ufficiali
e invia alert con strategie operative tradotte in italiano.

Fonti: ForexFactory, CoinDesk, CoinTelegraph, Reuters, Bloomberg RSS,
       Investing.com, CryptoSlate, The Block, CME FedWatch, CNBC
"""

import os, sys, time, json, re, hashlib, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
VERSION = "1.0.0"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "291291784")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SCAN_INTERVAL = 180  # 3 minuti
STATE_FILE = "state.json"
SEEN_FILE = "seen_news.json"

# Sessioni di mercato (UTC)
MARKET_SESSIONS = {
    "SYDNEY": {"open": 21, "close": 6, "emoji": "🇦🇺", "name": "Sydney"},
    "TOKYO": {"open": 0, "close": 9, "emoji": "🇯🇵", "name": "Tokyo"},
    "LONDRA": {"open": 7, "close": 16, "emoji": "🇬🇧", "name": "Londra"},
    "NEW_YORK": {"open": 13, "close": 22, "emoji": "🇺🇸", "name": "New York"},
}

# Traduzioni base EN->IT per termini finanziari comuni
TRANSLATIONS = {
    "gold": "Oro", "silver": "Argento", "copper": "Rame", "oil": "Petrolio",
    "crude": "Greggio", "natural gas": "Gas Naturale",
    "bitcoin": "Bitcoin", "ethereum": "Ethereum",
    "interest rate": "Tasso di Interesse", "inflation": "Inflazione",
    "unemployment": "Disoccupazione", "GDP": "PIL", "CPI": "IPC",
    "PPI": "IPP", "retail sales": "Vendite al Dettaglio",
    "non-farm payrolls": "Buste Paga Non Agricole", "NFP": "NFP",
    "federal reserve": "Federal Reserve", "ECB": "BCE",
    "rate hike": "Rialzo Tassi", "rate cut": "Taglio Tassi",
    "bullish": "rialzista", "bearish": "ribassista",
    "ETF": "ETF", "approval": "approvazione", "rejection": "rifiuto",
    "tariff": "dazio", "tariffs": "dazi", "trade war": "guerra commerciale",
    "recession": "recessione", "rally": "rally", "crash": "crollo",
    "breakout": "rottura", "support": "supporto", "resistance": "resistenza",
    "halving": "halving", "mining": "mining", "staking": "staking",
    "whale": "whale", "liquidation": "liquidazione",
}

log = logging.getLogger("NewsAlertBot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------------------------------
# UTILITY
# ---------------------------------------------------------------------------
def escape_html(t):
    if not t: return ""
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_telegram(text):
    if not TELEGRAM_TOKEN:
        log.warning("No TELEGRAM_TOKEN")
        return
    for chunk_start in range(0, len(text), 4000):
        chunk = text[chunk_start:chunk_start + 4000]
        try:
            data = {"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML",
                    "disable_web_page_preview": True}
            r = requests.post(f"{TELEGRAM_API}/sendMessage", json=data, timeout=15)
            if not r.ok:
                log.error(f"Telegram error: {r.text}")
        except Exception as e:
            log.error(f"Telegram send error: {e}")

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

def news_hash(title, source):
    return hashlib.md5(f"{title}:{source}".encode()).hexdigest()[:12]

def translate_to_italian(text):
    """Traduzione semplice EN->IT per termini finanziari chiave."""
    if not text:
        return text
    result = text
    for en, it in sorted(TRANSLATIONS.items(), key=lambda x: -len(x[0])):
        result = re.sub(re.escape(en), it, result, flags=re.IGNORECASE)
    return result

def get_impact_emoji(impact):
    """Converte livello impatto in emoji."""
    impact = str(impact).lower()
    if "high" in impact or "alto" in impact or impact == "3":
        return "🔴 ALTO"
    elif "medium" in impact or "medio" in impact or impact == "2":
        return "🟡 MEDIO"
    else:
        return "🟢 BASSO"

# ---------------------------------------------------------------------------
# PREZZI LIVE
# ---------------------------------------------------------------------------
def get_prices():
    """Ottieni prezzi live di crypto e materie prime."""
    prices = {}
    try:
        # Crypto da CoinGecko
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana,ripple", 
                    "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=10
        )
        if r.ok:
            data = r.json()
            for coin, vals in data.items():
                prices[coin] = {
                    "price": vals.get("usd", 0),
                    "change_24h": vals.get("usd_24h_change", 0)
                }
    except Exception as e:
        log.error(f"Errore prezzi crypto: {e}")

    try:
        # Materie prime da alternative API
        commodities = {
            "gold": "https://api.metals.live/v1/spot/gold",
            "silver": "https://api.metals.live/v1/spot/silver",
        }
        for name, url in commodities.items():
            try:
                r = requests.get(url, timeout=10)
                if r.ok:
                    data = r.json()
                    if isinstance(data, list) and len(data) > 0:
                        prices[name] = {"price": data[-1].get("price", 0), "change_24h": 0}
            except:
                pass
    except Exception as e:
        log.error(f"Errore prezzi commodities: {e}")

    return prices

# ---------------------------------------------------------------------------
# FONTI NOTIZIE
# ---------------------------------------------------------------------------

def fetch_forexfactory():
    """Scarica calendario economico da ForexFactory."""
    events = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.forexfactory.com/calendar", headers=headers, timeout=15)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("tr.calendar__row")
            for row in rows[:30]:
                try:
                    currency = row.select_one(".calendar__currency")
                    impact_el = row.select_one(".calendar__impact span")
                    event_el = row.select_one(".calendar__event-title")
                    time_el = row.select_one(".calendar__time")
                    actual_el = row.select_one(".calendar__actual")
                    forecast_el = row.select_one(".calendar__forecast")
                    previous_el = row.select_one(".calendar__previous")

                    if not event_el:
                        continue

                    impact_class = impact_el.get("class", []) if impact_el else []
                    impact = "high" if any("high" in c for c in impact_class) else \
                             "medium" if any("medium" in c for c in impact_class) else "low"

                    event = {
                        "source": "ForexFactory",
                        "title": event_el.get_text(strip=True),
                        "currency": currency.get_text(strip=True) if currency else "",
                        "impact": impact,
                        "time": time_el.get_text(strip=True) if time_el else "",
                        "actual": actual_el.get_text(strip=True) if actual_el else "",
                        "forecast": forecast_el.get_text(strip=True) if forecast_el else "",
                        "previous": previous_el.get_text(strip=True) if previous_el else "",
                        "type": "CALENDARIO"
                    }
                    events.append(event)
                except:
                    continue
    except Exception as e:
        log.error(f"Errore ForexFactory: {e}")

    # Fallback: ForexFactory API JSON
    if not events:
        try:
            today = datetime.now(timezone.utc).strftime("%b%d.%Y").lower()
            r = requests.get(f"https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
            if r.ok:
                data = r.json()
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                for item in data:
                    event_date = item.get("date", "")[:10]
                    if event_date == today_str or event_date == "":
                        events.append({
                            "source": "ForexFactory",
                            "title": item.get("title", ""),
                            "currency": item.get("country", ""),
                            "impact": item.get("impact", "Low"),
                            "time": item.get("date", "")[11:16] if len(item.get("date", "")) > 11 else "",
                            "actual": item.get("actual", ""),
                            "forecast": item.get("forecast", ""),
                            "previous": item.get("previous", ""),
                            "type": "CALENDARIO"
                        })
        except Exception as e:
            log.error(f"Errore ForexFactory fallback: {e}")

    return events

def fetch_coindesk():
    """Notizie da CoinDesk RSS."""
    news = []
    try:
        r = requests.get("https://www.coindesk.com/arc/outboundfeeds/rss/", timeout=10)
        if r.ok:
            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")[:10]
            for item in items:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link = item.find("link").get_text(strip=True) if item.find("link") else ""
                desc = item.find("description").get_text(strip=True) if item.find("description") else ""
                pub = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                news.append({
                    "source": "CoinDesk",
                    "title": title,
                    "description": desc[:300],
                    "link": link,
                    "published": pub,
                    "type": "CRYPTO"
                })
    except Exception as e:
        log.error(f"Errore CoinDesk: {e}")
    return news

def fetch_cointelegraph():
    """Notizie da CoinTelegraph RSS."""
    news = []
    try:
        r = requests.get("https://cointelegraph.com/rss", timeout=10)
        if r.ok:
            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")[:10]
            for item in items:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link = item.find("link").get_text(strip=True) if item.find("link") else ""
                desc = item.find("description").get_text(strip=True) if item.find("description") else ""
                pub = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                news.append({
                    "source": "CoinTelegraph",
                    "title": title,
                    "description": desc[:300],
                    "link": link,
                    "published": pub,
                    "type": "CRYPTO"
                })
    except Exception as e:
        log.error(f"Errore CoinTelegraph: {e}")
    return news

def fetch_cryptopanic():
    """Notizie aggregate da CryptoPanic (free API)."""
    news = []
    try:
        r = requests.get("https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&kind=news",
                         timeout=10)
        if r.ok:
            data = r.json()
            for item in data.get("results", [])[:10]:
                title = item.get("title", "")
                url = item.get("url", "")
                source_name = item.get("source", {}).get("title", "CryptoPanic")
                pub = item.get("published_at", "")
                news.append({
                    "source": source_name,
                    "title": title,
                    "description": "",
                    "link": url,
                    "published": pub,
                    "type": "CRYPTO"
                })
    except Exception as e:
        log.error(f"Errore CryptoPanic: {e}")
    return news

def fetch_investing_news():
    """Notizie da Investing.com RSS (commodities + macro)."""
    news = []
    feeds = [
        ("https://www.investing.com/rss/news_301.rss", "COMMODITIES"),  # Commodities
        ("https://www.investing.com/rss/news_1.rss", "MACRO"),  # Top news
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    for url, cat in feeds:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.ok:
                soup = BeautifulSoup(r.text, "xml")
                items = soup.find_all("item")[:5]
                for item in items:
                    title = item.find("title").get_text(strip=True) if item.find("title") else ""
                    link = item.find("link").get_text(strip=True) if item.find("link") else ""
                    desc = item.find("description").get_text(strip=True) if item.find("description") else ""
                    pub = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
                    news.append({
                        "source": "Investing.com",
                        "title": title,
                        "description": desc[:300],
                        "link": link,
                        "published": pub,
                        "type": cat
                    })
        except Exception as e:
            log.error(f"Errore Investing.com {cat}: {e}")
    return news

def fetch_reuters_rss():
    """Notizie da Reuters RSS."""
    news = []
    try:
        r = requests.get("https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.ok:
            soup = BeautifulSoup(r.text, "xml")
            items = soup.find_all("item")[:5]
            for item in items:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link = item.find("link").get_text(strip=True) if item.find("link") else ""
                desc = item.find("description").get_text(strip=True) if item.find("description") else ""
                news.append({
                    "source": "Reuters",
                    "title": title,
                    "description": desc[:300],
                    "link": link,
                    "published": "",
                    "type": "MACRO"
                })
    except Exception as e:
        log.error(f"Errore Reuters: {e}")
    return news

# ---------------------------------------------------------------------------
# ANALISI IMPATTO E STRATEGIA
# ---------------------------------------------------------------------------

IMPACT_KEYWORDS = {
    "high": [
        "etf", "approval", "reject", "sec", "fed", "rate", "hike", "cut",
        "inflation", "cpi", "gdp", "recession", "crash", "hack", "ban",
        "regulation", "tariff", "war", "sanctions", "halving", "default",
        "liquidat", "billion", "trillion", "emergency", "collapse",
        "breaking", "urgent", "flash", "surge", "plunge", "soar", "dump",
        "all-time high", "ath", "record", "unprecedented"
    ],
    "medium": [
        "partnership", "launch", "update", "upgrade", "fork", "mining",
        "staking", "whale", "exchange", "listing", "delist", "audit",
        "report", "earnings", "revenue", "profit", "loss", "forecast",
        "outlook", "analysis", "survey", "poll", "vote", "election"
    ]
}

def assess_impact(title, description=""):
    """Valuta l'impatto di una notizia."""
    text = f"{title} {description}".lower()
    high_count = sum(1 for kw in IMPACT_KEYWORDS["high"] if kw in text)
    med_count = sum(1 for kw in IMPACT_KEYWORDS["medium"] if kw in text)

    if high_count >= 2:
        return 5, "🔴🔴🔴🔴🔴 CRITICO"
    elif high_count >= 1:
        return 4, "🔴🔴🔴🔴 MOLTO ALTO"
    elif med_count >= 2:
        return 3, "🟡🟡🟡 ALTO"
    elif med_count >= 1:
        return 2, "🟡🟡 MEDIO"
    else:
        return 1, "🟢 BASSO"

def determine_affected_assets(title, description=""):
    """Determina quali asset sono impattati dalla notizia."""
    text = f"{title} {description}".lower()
    assets = []
    asset_map = {
        "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
        "solana": "SOL", "sol": "SOL", "ripple": "XRP", "xrp": "XRP",
        "gold": "ORO", "oro": "ORO", "silver": "ARGENTO", "argento": "ARGENTO",
        "copper": "RAME", "rame": "RAME", "oil": "PETROLIO", "crude": "PETROLIO",
        "natural gas": "GAS", "dollar": "USD", "euro": "EUR",
        "s&p": "S&P500", "nasdaq": "NASDAQ", "dow": "DOW",
    }
    for kw, asset in asset_map.items():
        if kw in text and asset not in assets:
            assets.append(asset)
    return assets

def generate_strategy(title, description, impact_score, assets, prices):
    """Genera strategia operativa basata sulla notizia."""
    text = f"{title} {description}".lower()

    # Determina sentiment
    bullish_words = ["approval", "approv", "launch", "partner", "surge", "rally",
                     "bullish", "record", "ath", "all-time", "cut", "stimulus",
                     "adopt", "accept", "soar", "gain", "positive", "growth"]
    bearish_words = ["reject", "ban", "crash", "hack", "dump", "bearish",
                     "recession", "default", "collapse", "plunge", "negative",
                     "loss", "decline", "sanction", "war", "tariff", "hike"]

    bull = sum(1 for w in bullish_words if w in text)
    bear = sum(1 for w in bearish_words if w in text)

    if bull > bear:
        sentiment = "RIALZISTA"
        direction = "BUY"
        emoji = "🟢"
    elif bear > bull:
        sentiment = "RIBASSISTA"
        direction = "SELL"
        emoji = "🔴"
    else:
        sentiment = "NEUTRO"
        direction = "ATTENDI"
        emoji = "⚪"

    strategy = f"\n{'━' * 30}\n"
    strategy += f"{emoji} <b>SENTIMENT: {sentiment}</b>\n\n"

    if direction != "ATTENDI" and assets:
        strategy += f"⚡ <b>STRATEGIA OPERATIVA:</b>\n"
        for asset in assets[:3]:
            price_info = ""
            for k, v in prices.items():
                if asset.lower() in k.lower() or k.lower() in asset.lower():
                    p = v.get("price", 0)
                    ch = v.get("change_24h", 0)
                    price_info = f" (${p:,.2f}, {ch:+.1f}%)"
                    break

            if direction == "BUY":
                strategy += f"   🟢 <b>{asset}</b>{price_info}: COMPRA\n"
                if impact_score >= 4:
                    strategy += f"   📊 Tipo: SPOT accumulo + SCALPING long\n"
                    strategy += f"   💰 Importo: 10-30€ (conservativo)\n"
                else:
                    strategy += f"   📊 Tipo: SPOT accumulo graduale\n"
                    strategy += f"   💰 Importo: 5-15€\n"
            else:
                strategy += f"   🔴 <b>{asset}</b>{price_info}: VENDI/SHORT\n"
                if impact_score >= 4:
                    strategy += f"   📊 Tipo: Riduci esposizione + SCALPING short\n"
                    strategy += f"   ⚠️ Proteggi capitale, SL stretto\n"
                else:
                    strategy += f"   📊 Tipo: Cautela, riduci posizioni\n"
            strategy += "\n"
    elif direction == "ATTENDI":
        strategy += f"   ⏳ <b>ATTENDI</b>: notizia ambigua, aspetta conferma dal mercato\n"
        strategy += f"   📊 Non aprire posizioni, monitora la reazione\n"

    if impact_score >= 4:
        strategy += f"   ⚠️ <b>ATTENZIONE:</b> Notizia ad altissimo impatto!\n"
        strategy += f"   Aspettati volatilità elevata nelle prossime 1-4 ore\n"

    return strategy

# ---------------------------------------------------------------------------
# SESSIONI DI MERCATO
# ---------------------------------------------------------------------------
def check_market_sessions(state):
    """Controlla apertura/chiusura sessioni e invia alert."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    minute = now.minute
    alerts = []

    for key, session in MARKET_SESSIONS.items():
        open_h = session["open"]
        close_h = session["close"]
        name = session["name"]
        emoji = session["emoji"]

        # Alert apertura (entro 5 min dall'apertura)
        open_key = f"session_open_{key}_{now.strftime('%Y%m%d')}"
        if hour == open_h and minute < 5 and open_key not in state.get("sent_sessions", []):
            alerts.append(
                f"🔔 <b>APERTURA MERCATO</b>\n\n"
                f"{emoji} <b>Sessione {name} APERTA</b>\n"
                f"⏰ Orario: {open_h}:00 - {close_h}:00 UTC\n\n"
                f"💡 <b>Cosa aspettarsi:</b>\n"
            )
            if key == "LONDRA":
                alerts[-1] += (
                    "   • Aumento volatilità su EUR, GBP, ORO\n"
                    "   • Overlap con Tokyo = movimenti forti\n"
                    "   • Attenzione ai dati macro europei\n"
                )
            elif key == "NEW_YORK":
                alerts[-1] += (
                    "   • Massima volatilità (overlap con Londra)\n"
                    "   • Dati USA: NFP, CPI, Fed speakers\n"
                    "   • Movimenti forti su USD, ORO, BTC\n"
                    "   • Orari chiave: 13:30-15:00 UTC\n"
                )
            elif key == "TOKYO":
                alerts[-1] += (
                    "   • Movimenti su JPY, AUD, NZD\n"
                    "   • Crypto spesso attive in questa sessione\n"
                )
            elif key == "SYDNEY":
                alerts[-1] += (
                    "   • Inizio settimana di trading\n"
                    "   • Bassa liquidità, spread più ampi\n"
                )
            state.setdefault("sent_sessions", []).append(open_key)

        # Alert chiusura (entro 5 min dalla chiusura)
        close_key = f"session_close_{key}_{now.strftime('%Y%m%d')}"
        if hour == close_h and minute < 5 and close_key not in state.get("sent_sessions", []):
            alerts.append(
                f"🔕 <b>CHIUSURA MERCATO</b>\n\n"
                f"{emoji} <b>Sessione {name} CHIUSA</b>\n"
            )
            state.setdefault("sent_sessions", []).append(close_key)

    return alerts

# ---------------------------------------------------------------------------
# FORMATO ALERT
# ---------------------------------------------------------------------------
def format_calendar_alert(event):
    """Formatta alert calendario economico (ForexFactory)."""
    impact = get_impact_emoji(event.get("impact", "low"))
    title_it = translate_to_italian(event.get("title", ""))
    currency = event.get("currency", "")
    time_str = event.get("time", "")
    actual = event.get("actual", "")
    forecast = event.get("forecast", "")
    previous = event.get("previous", "")

    msg = (
        f"📅 <b>EVENTO ECONOMICO</b>\n"
        f"{'━' * 30}\n"
        f"📊 <b>{escape_html(title_it)}</b>\n"
        f"💱 Valuta: {escape_html(currency)} | Impatto: {impact}\n"
    )
    if time_str:
        msg += f"⏰ Orario: {escape_html(time_str)} UTC\n"
    if actual:
        msg += f"📈 Attuale: <b>{escape_html(actual)}</b>"
        if forecast:
            msg += f" (Previsto: {escape_html(forecast)})"
        if previous:
            msg += f" (Precedente: {escape_html(previous)})"
        msg += "\n"
    elif forecast:
        msg += f"📊 Previsto: {escape_html(forecast)}"
        if previous:
            msg += f" | Precedente: {escape_html(previous)}"
        msg += "\n"

    msg += f"\n📰 Fonte: ForexFactory\n"
    return msg

def format_news_alert(news_item, impact_score, impact_label, assets, strategy, prices):
    """Formatta alert notizia con strategia."""
    source = news_item.get("source", "")
    title = news_item.get("title", "")
    desc = news_item.get("description", "")
    link = news_item.get("link", "")
    news_type = news_item.get("type", "")

    # Traduzione
    title_it = translate_to_italian(title)
    desc_it = translate_to_italian(desc)

    # Emoji tipo
    type_emoji = {
        "CRYPTO": "🪙", "COMMODITIES": "🥇", "MACRO": "🏦",
        "CALENDARIO": "📅", "GEOPOLITICA": "🌍"
    }.get(news_type, "📰")

    # Header impatto
    if impact_score >= 4:
        header = f"🚨🚨🚨 <b>ALERT CRITICO</b> 🚨🚨🚨\n{'━' * 30}\n"
    elif impact_score >= 3:
        header = f"🔔🔔 <b>ALERT IMPORTANTE</b> 🔔🔔\n{'━' * 30}\n"
    else:
        header = f"🔔 <b>ALERT NOTIZIA</b>\n{'━' * 30}\n"

    msg = header
    msg += f"{type_emoji} <b>{escape_html(title_it)}</b>\n\n"

    if desc_it:
        # Pulisci HTML dalla descrizione
        clean_desc = re.sub(r'<[^>]+>', '', desc_it)[:400]
        msg += f"📝 {escape_html(clean_desc)}\n\n"

    msg += f"📊 Impatto: {impact_label}\n"

    if assets:
        msg += f"💎 Asset impattati: {', '.join(assets)}\n"

    # Prezzi live degli asset impattati
    for asset in assets[:3]:
        for k, v in prices.items():
            if asset.lower() in k.lower() or k.lower() in asset.lower():
                p = v.get("price", 0)
                ch = v.get("change_24h", 0)
                ch_emoji = "📈" if ch > 0 else "📉" if ch < 0 else "➡️"
                msg += f"   {ch_emoji} {asset}: ${p:,.2f} ({ch:+.1f}%)\n"
                break

    msg += strategy

    if link:
        msg += f"\n🔗 <a href=\"{escape_html(link)}\">Leggi fonte originale</a>\n"
    msg += f"📰 Fonte: {escape_html(source)}\n"

    return msg

# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
def main():
    log.info(f"NewsAlertBot v{VERSION} avviato")

    state = load_json(STATE_FILE, {})
    seen = load_json(SEEN_FILE, {"hashes": [], "last_clean": ""})

    # Benvenuto
    if not state.get("welcome_sent"):
        prices = get_prices()
        btc_p = prices.get("bitcoin", {}).get("price", 0)
        eth_p = prices.get("ethereum", {}).get("price", 0)

        welcome = (
            f"✅ <b>NewsAlertBot v{VERSION} avviato!</b>\n\n"
            f"<b>FONTI MONITORATE:</b>\n"
            f"📅 ForexFactory (calendario economico)\n"
            f"🪙 CoinDesk + CoinTelegraph + CryptoPanic\n"
            f"🏦 Investing.com (macro + commodities)\n"
            f"📰 Reuters (finanza globale)\n\n"
            f"<b>ALERT ATTIVI:</b>\n"
            f"🔔 Notizie ad alto impatto in tempo reale\n"
            f"📅 Eventi economici ForexFactory\n"
            f"🏛️ Apertura/chiusura sessioni di mercato\n"
            f"⚡ Strategie operative per ogni notizia\n"
            f"🇮🇹 Tutto tradotto in italiano\n\n"
            f"<b>SESSIONI MERCATO (UTC):</b>\n"
            f"🇦🇺 Sydney: 21:00 - 06:00\n"
            f"🇯🇵 Tokyo: 00:00 - 09:00\n"
            f"🇬🇧 Londra: 07:00 - 16:00\n"
            f"🇺🇸 New York: 13:00 - 22:00\n\n"
            f"<b>MERCATO ORA:</b>\n"
            f"🟠 BTC: ${btc_p:,.0f}\n"
            f"🔵 ETH: ${eth_p:,.0f}\n\n"
            f"⏱️ Scansione: ogni {SCAN_INTERVAL // 60} minuti\n"
            f"<i>NewsAlertBot v{VERSION}</i>"
        )
        send_telegram(welcome)
        state["welcome_sent"] = True
        save_json(STATE_FILE, state)

    cycle = 0
    while True:
        try:
            cycle += 1
            log.info(f"--- Ciclo {cycle} ---")
            now = datetime.now(timezone.utc)

            # Pulisci seen ogni 24h
            if seen.get("last_clean", "") != now.strftime("%Y-%m-%d"):
                seen["hashes"] = seen["hashes"][-500:]  # Tieni ultimi 500
                seen["last_clean"] = now.strftime("%Y-%m-%d")
                # Pulisci sessioni vecchie
                state["sent_sessions"] = [s for s in state.get("sent_sessions", [])
                                           if now.strftime("%Y%m%d") in s]

            prices = get_prices()

            # 1. SESSIONI DI MERCATO
            session_alerts = check_market_sessions(state)
            for alert in session_alerts:
                send_telegram(alert)
                time.sleep(1)

            # 2. CALENDARIO FOREXFACTORY
            ff_events = fetch_forexfactory()
            high_events = [e for e in ff_events if "high" in str(e.get("impact", "")).lower()]
            for event in high_events[:5]:
                h = news_hash(event["title"], "ForexFactory")
                if h not in seen["hashes"]:
                    msg = format_calendar_alert(event)
                    # Aggiungi strategia per eventi ad alto impatto
                    assets = determine_affected_assets(event["title"])
                    if event.get("actual") and event.get("forecast"):
                        try:
                            actual_num = float(re.sub(r'[^\d.\-]', '', event["actual"]))
                            forecast_num = float(re.sub(r'[^\d.\-]', '', event["forecast"]))
                            if actual_num > forecast_num:
                                msg += f"\n✅ <b>DATO SOPRA LE ATTESE</b> → Possibile rialzo {event.get('currency', '')}\n"
                            elif actual_num < forecast_num:
                                msg += f"\n❌ <b>DATO SOTTO LE ATTESE</b> → Possibile ribasso {event.get('currency', '')}\n"
                        except:
                            pass
                    send_telegram(msg)
                    seen["hashes"].append(h)
                    time.sleep(1)

            # 3. NOTIZIE DA TUTTE LE FONTI
            all_news = []
            all_news.extend(fetch_coindesk())
            all_news.extend(fetch_cointelegraph())
            all_news.extend(fetch_cryptopanic())
            all_news.extend(fetch_investing_news())
            all_news.extend(fetch_reuters_rss())

            # Filtra e ordina per impatto
            scored_news = []
            for item in all_news:
                h = news_hash(item["title"], item["source"])
                if h in seen["hashes"]:
                    continue
                score, label = assess_impact(item["title"], item.get("description", ""))
                scored_news.append((score, label, item, h))

            scored_news.sort(key=lambda x: -x[0])

            # Invia solo notizie con impatto >= 3 (ALTO+)
            sent_this_cycle = 0
            for score, label, item, h in scored_news:
                if score < 3:
                    continue
                if sent_this_cycle >= 5:
                    break

                assets = determine_affected_assets(item["title"], item.get("description", ""))
                strategy = generate_strategy(
                    item["title"], item.get("description", ""),
                    score, assets, prices
                )
                msg = format_news_alert(item, score, label, assets, strategy, prices)
                send_telegram(msg)
                seen["hashes"].append(h)
                sent_this_cycle += 1
                time.sleep(2)

            save_json(SEEN_FILE, seen)
            save_json(STATE_FILE, state)

            log.info(f"Ciclo {cycle} completato. FF events: {len(high_events)}, "
                     f"News inviate: {sent_this_cycle}. Prossima tra {SCAN_INTERVAL}s")

        except Exception as e:
            log.error(f"Errore ciclo: {e}", exc_info=True)

        for _ in range(SCAN_INTERVAL // 10):
            time.sleep(10)

if __name__ == "__main__":
    main()
