#!/usr/bin/env python3
"""
TradingAlertBot v2.2 - Alert Notizie & Trading Live per Fabio
Bot Telegram: notizie impattanti, segnali trading crypto/commodities,
sessioni di mercato, strategie operative, allocazione consigliata,
analisi impatto AI per ogni notizia. Tutto in italiano, orari italiani.

Fonti: ForexFactory, CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine,
       Investing.com, Bloomberg, CNBC, FXStreet, MarketWatch, CoinGecko
"""

import os, sys, time, json, re, hashlib, logging, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
VERSION = "2.2.0"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8315107370:AAHA12xJb6VIklrhZj93OYoa2FyI8937H_U")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "291291784")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Fuso orario Italia (CEST = UTC+2)
IT_OFFSET = timedelta(hours=2)

SCAN_NEWS = 180       # 3 min - notizie
SCAN_PRICES = 300     # 5 min - prezzi e segnali trading
SCAN_SESSIONS = 60    # 1 min - sessioni mercato

STATE_FILE = "state.json"
SEEN_FILE = "seen_news.json"
PRICE_HISTORY_FILE = "price_history.json"

# R:R minimo per segnali scalping/daytrading
MIN_RR = 5.0

# Sessioni di mercato (orari UTC, mostrati in italiano)
MARKET_SESSIONS = {
    "SYDNEY":   {"open_utc": 21, "close_utc": 6,  "emoji": "🇦🇺", "name": "Sydney",   "assets": "AUD, NZD, JPY"},
    "TOKYO":    {"open_utc": 0,  "close_utc": 9,  "emoji": "🇯🇵", "name": "Tokyo",    "assets": "JPY, AUD, Crypto"},
    "LONDRA":   {"open_utc": 7,  "close_utc": 16, "emoji": "🇬🇧", "name": "Londra",   "assets": "EUR, GBP, ORO, ARGENTO"},
    "NEW_YORK": {"open_utc": 13, "close_utc": 22, "emoji": "🇺🇸", "name": "New York", "assets": "USD, ORO, PETROLIO, S&P500, BTC"},
}

# Asset monitorati
CRYPTO_IDS = "bitcoin,ethereum,solana,ripple,cardano,avalanche-2,chainlink,polkadot,ondo-finance"
CRYPTO_SYMBOLS = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
    "cardano": "ADA", "avalanche-2": "AVAX", "chainlink": "LINK", "polkadot": "DOT",
    "ondo-finance": "ONDO"
}

log = logging.getLogger("TradingAlertBot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------------------------------
# UTILITY
# ---------------------------------------------------------------------------
def now_italy():
    return datetime.now(timezone.utc) + IT_OFFSET

def format_time_it(dt=None):
    if dt is None:
        dt = now_italy()
    return dt.strftime("%H:%M")

def format_datetime_it(dt=None):
    if dt is None:
        dt = now_italy()
    return dt.strftime("%d/%m/%Y %H:%M")

def utc_to_italy(hour_utc):
    h = (hour_utc + 2) % 24
    return f"{h:02d}:00"

def escape_html(t):
    if not t: return ""
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_telegram(text):
    if not TELEGRAM_TOKEN:
        log.warning("No TELEGRAM_TOKEN")
        return False
    ok = True
    for i in range(0, len(text), 4000):
        chunk = text[i:i+4000]
        try:
            r = requests.post(f"{TELEGRAM_API}/sendMessage",
                              json={"chat_id": CHAT_ID, "text": chunk,
                                    "parse_mode": "HTML", "disable_web_page_preview": True},
                              timeout=15)
            if not r.ok:
                log.error(f"TG err (HTML): {r.text[:200]}")
                # Fallback senza HTML
                r2 = requests.post(f"{TELEGRAM_API}/sendMessage",
                                   json={"chat_id": CHAT_ID, "text": chunk,
                                         "disable_web_page_preview": True},
                                   timeout=15)
                if not r2.ok:
                    log.error(f"TG err (plain): {r2.text[:200]}")
                    ok = False
        except Exception as e:
            log.error(f"TG send err: {e}")
            ok = False
        time.sleep(0.5)
    return ok

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Save JSON err: {e}")

def news_hash(title, source):
    return hashlib.md5(f"{title}:{source}".encode()).hexdigest()[:12]

def safe_get(url, params=None, headers=None, timeout=12):
    try:
        h = headers or {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, params=params, headers=h, timeout=timeout)
        if r.ok:
            return r
        else:
            log.warning(f"GET {url[:60]}: HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"GET {url[:60]}: {e}")
    return None

# ---------------------------------------------------------------------------
# TRADUZIONE GRATUITA (Google Translate, no API key)
# ---------------------------------------------------------------------------
_translate_cache = {}

def translate(text):
    """Traduce testo in italiano usando Google Translate gratuito."""
    if not text or len(text.strip()) < 3:
        return text
    # Se è già italiano
    it_words = ["il ", "la ", "di ", "che ", "per ", "con ", "una ", "del ", "nel ", " e ", " sono "]
    lower = text.lower()
    it_count = sum(1 for w in it_words if w in lower)
    if it_count >= 3:
        return text
    # Cache
    cache_key = text[:150]
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "it", "dt": "t", "q": text[:1000]}
        r = requests.get(url, params=params, timeout=10)
        if r.ok:
            result = r.json()
            translated = "".join(part[0] for part in result[0] if part[0])
            _translate_cache[cache_key] = translated
            return translated
    except Exception as e:
        log.warning(f"Traduzione fallita: {e}")
    return text

# Filtro orari: non inviare notizie tra 00:00 e 06:00 ora italiana
QUIET_START = 0
QUIET_END = 6

def is_quiet_hours():
    h = now_italy().hour
    return QUIET_START <= h < QUIET_END

# ---------------------------------------------------------------------------
# PREZZI LIVE
# ---------------------------------------------------------------------------
def get_crypto_prices():
    prices = {}
    r = safe_get("https://api.coingecko.com/api/v3/simple/price",
                 params={"ids": CRYPTO_IDS, "vs_currencies": "usd",
                         "include_24hr_change": "true", "include_24hr_vol": "true"})
    if r:
        for coin, v in r.json().items():
            sym = CRYPTO_SYMBOLS.get(coin, coin.upper())
            prices[sym] = {
                "price": v.get("usd", 0),
                "change_24h": v.get("usd_24h_change", 0),
                "volume_24h": v.get("usd_24h_vol", 0),
                "id": coin
            }
    return prices

def get_commodity_prices():
    prices = {}
    r = safe_get("https://api.coingecko.com/api/v3/simple/price",
                 params={"ids": "tether-gold,silver-token", "vs_currencies": "usd",
                         "include_24hr_change": "true"})
    if r:
        data = r.json()
        if "tether-gold" in data:
            prices["ORO"] = {"price": data["tether-gold"].get("usd", 0),
                             "change_24h": data["tether-gold"].get("usd_24h_change", 0)}
    if "ORO" not in prices:
        r2 = safe_get("https://api.frankfurter.app/latest?from=XAU&to=USD")
        if r2:
            data2 = r2.json()
            usd = data2.get("rates", {}).get("USD", 0)
            if usd: prices["ORO"] = {"price": usd, "change_24h": 0}
    return prices

def get_fear_greed():
    r = safe_get("https://api.alternative.me/fng/?limit=2")
    if r:
        data = r.json().get("data", [])
        if len(data) >= 2:
            now_val = int(data[0]["value"])
            prev_val = int(data[1]["value"])
            label = data[0]["value_classification"]
            return {"value": now_val, "previous": prev_val, "label": label}
    return None

# ---------------------------------------------------------------------------
# ANALISI TECNICA SEMPLIFICATA
# ---------------------------------------------------------------------------
def get_technical_analysis(coin_id):
    r = safe_get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                 params={"vs_currency": "usd", "days": "7", "interval": "hourly"})
    if not r:
        return None
    data = r.json()
    prices_list = [p[1] for p in data.get("prices", [])]
    if len(prices_list) < 30:
        return None

    # RSI(14)
    closes = prices_list[-15:]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0: gains.append(diff); losses.append(0)
        else: gains.append(0); losses.append(abs(diff))
    avg_gain = sum(gains) / 14 if gains else 0.001
    avg_loss = sum(losses) / 14 if losses else 0.001
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    price_now = prices_list[-1]
    price_24h = prices_list[-24] if len(prices_list) >= 24 else prices_list[0]
    price_7d = prices_list[0]
    ma_24 = sum(prices_list[-24:]) / min(24, len(prices_list[-24:]))
    ma_168 = sum(prices_list) / len(prices_list)
    support = min(prices_list[-168:]) if len(prices_list) >= 168 else min(prices_list)
    resistance = max(prices_list[-168:]) if len(prices_list) >= 168 else max(prices_list)

    trend_24h = "UP" if price_now > price_24h else "DOWN"
    trend_7d = "UP" if price_now > price_7d else "DOWN"
    above_ma24 = price_now > ma_24
    above_ma168 = price_now > ma_168

    return {
        "rsi": round(rsi, 1), "price": price_now,
        "trend_24h": trend_24h, "trend_7d": trend_7d,
        "above_ma24": above_ma24, "above_ma168": above_ma168,
        "support": round(support, 2), "resistance": round(resistance, 2),
        "ma24": round(ma_24, 2),
        "change_24h": round((price_now - price_24h) / price_24h * 100, 2) if price_24h else 0,
        "change_7d": round((price_now - price_7d) / price_7d * 100, 2) if price_7d else 0,
    }

# ---------------------------------------------------------------------------
# FONTI NOTIZIE
# ---------------------------------------------------------------------------
def fetch_forexfactory():
    events = []
    r = safe_get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if r:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for item in r.json():
            d = item.get("date", "")[:10]
            if d == today_str:
                events.append({
                    "source": "ForexFactory",
                    "title": item.get("title", ""),
                    "currency": item.get("country", ""),
                    "impact": item.get("impact", "Low"),
                    "time_utc": item.get("date", "")[11:16] if len(item.get("date", "")) > 11 else "",
                    "actual": item.get("actual", ""),
                    "forecast": item.get("forecast", ""),
                    "previous": item.get("previous", ""),
                    "type": "CALENDARIO"
                })
    log.info(f"ForexFactory: {len(events)} eventi oggi")
    return events

def fetch_rss(url, source, news_type, limit=8):
    news = []
    r = safe_get(url)
    if r:
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.find_all("item")[:limit]:
            title = item.find("title").get_text(strip=True) if item.find("title") else ""
            link = item.find("link")
            link_text = ""
            if link:
                link_text = link.get_text(strip=True) if link.string else (link.next_sibling.strip() if link.next_sibling and isinstance(link.next_sibling, str) else "")
            desc = item.find("description")
            desc_text = desc.get_text(strip=True)[:300] if desc else ""
            pub = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else ""
            if title:
                news.append({"source": source, "title": title, "description": desc_text,
                             "link": link_text, "published": pub, "type": news_type})
    log.info(f"RSS {source}: {len(news)} notizie")
    return news

def fetch_all_news():
    all_news = []
    # --- CRYPTO ---
    all_news.extend(fetch_rss("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", "CRYPTO"))
    all_news.extend(fetch_rss("https://cointelegraph.com/rss", "CoinTelegraph", "CRYPTO"))
    all_news.extend(fetch_rss("https://decrypt.co/feed", "Decrypt", "CRYPTO", 5))
    all_news.extend(fetch_rss("https://bitcoinmagazine.com/.rss/full/", "Bitcoin Magazine", "CRYPTO", 5))
    # --- MACRO / FOREX / COMMODITIES ---
    all_news.extend(fetch_rss("https://www.investing.com/rss/news_301.rss", "Investing.com", "COMMODITIES", 5))
    all_news.extend(fetch_rss("https://www.investing.com/rss/news_1.rss", "Investing.com", "MACRO", 5))
    all_news.extend(fetch_rss("https://www.investing.com/rss/news_14.rss", "Investing.com", "FOREX", 5))
    all_news.extend(fetch_rss("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch", "MACRO", 5))
    all_news.extend(fetch_rss("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC", "MACRO", 5))
    all_news.extend(fetch_rss("https://www.fxstreet.com/rss", "FXStreet", "FOREX", 5))
    all_news.extend(fetch_rss("https://feeds.bloomberg.com/markets/news.rss", "Bloomberg", "MACRO", 5))
    # --- CryptoPanic (no auth) ---
    try:
        r = safe_get("https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&kind=news")
        if r:
            for item in r.json().get("results", [])[:8]:
                all_news.append({
                    "source": item.get("source", {}).get("title", "CryptoPanic"),
                    "title": item.get("title", ""),
                    "description": "", "link": item.get("url", ""),
                    "published": item.get("published_at", ""), "type": "CRYPTO"
                })
    except:
        pass
    log.info(f"TOTALE notizie raccolte: {len(all_news)}")
    return all_news

# ---------------------------------------------------------------------------
# VALUTAZIONE IMPATTO
# ---------------------------------------------------------------------------
HIGH_KW = ["etf", "approval", "reject", "sec", "fed", "rate", "hike", "cut",
           "inflation", "cpi", "gdp", "recession", "crash", "hack", "ban",
           "regulation", "tariff", "war", "sanctions", "halving", "default",
           "liquidat", "billion", "trillion", "emergency", "collapse",
           "breaking", "urgent", "flash", "surge", "plunge", "soar",
           "all-time high", "ath", "record", "unprecedented", "dump",
           "blackrock", "securitize", "spac", "cept", "tokeniz", "rwa",
           "ondo", "fidelity", "vanguard", "grayscale", "microstrategy",
           "ipo", "acquisition", "merger", "buyback"]
MED_KW = ["partnership", "launch", "update", "upgrade", "fork", "mining",
          "staking", "whale", "exchange", "listing", "delist", "audit",
          "report", "earnings", "revenue", "profit", "loss", "forecast",
          "outlook", "analysis", "survey", "poll", "vote", "election",
          "fund", "institutional", "custody", "defi", "layer", "airdrop",
          "stablecoin", "usdt", "usdc", "tether", "circle"]

def assess_impact(title, desc=""):
    text = f"{title} {desc}".lower()
    h = sum(1 for kw in HIGH_KW if kw in text)
    m = sum(1 for kw in MED_KW if kw in text)
    if h >= 2: return 5, "🔴🔴🔴🔴🔴 CRITICO"
    if h >= 1: return 4, "🔴🔴🔴🔴 MOLTO ALTO"
    if m >= 2: return 3, "🟡🟡🟡 ALTO"
    if m >= 1: return 2, "🟡🟡 MEDIO"
    return 1, "🟢 BASSO"

def find_affected_assets(title, desc=""):
    text = f"{title} {desc}".lower()
    assets = []
    mapping = {
        "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
        "solana": "SOL", "ripple": "XRP", "cardano": "ADA", "avalanche": "AVAX",
        "chainlink": "LINK", "polkadot": "DOT",
        "gold": "ORO", "oro": "ORO", "silver": "ARGENTO", "argento": "ARGENTO",
        "copper": "RAME", "rame": "RAME", "oil": "PETROLIO", "crude": "PETROLIO",
        "natural gas": "GAS", "dollar": "USD", "euro": "EUR",
        "s&p": "S&P500", "nasdaq": "NASDAQ",
        "blackrock": "BLACKROCK", "securitize": "SECURITIZE", "spac": "SPAC",
        "cept": "CEPT", "ondo": "ONDO", "rwa": "RWA", "tokeniz": "RWA",
        "fidelity": "FIDELITY", "grayscale": "GRAYSCALE",
        "microstrategy": "MSTR", "tesla": "TSLA", "nvidia": "NVDA",
    }
    for kw, asset in mapping.items():
        if kw in text and asset not in assets:
            assets.append(asset)
    return assets

# ---------------------------------------------------------------------------
# ANALISI IMPATTO NOTIZIA (rule-based AI)
# ---------------------------------------------------------------------------

# Database di scenari per tipo di notizia
IMPACT_SCENARIOS = {
    # --- REGOLAMENTAZIONE ---
    "ban": {
        "analysis": "Un divieto normativo crea pressione ribassista immediata sull'asset coinvolto. Storicamente, i ban locali/nazionali hanno un impatto del -3/8% nel breve termine, ma l'effetto si attenua in 1-2 settimane se non seguito da altri paesi.",
        "opportunity": "Accumulo aggressivo dopo il panic sell iniziale (-5% o più dal prezzo pre-notizia)",
        "risk": "Effetto domino se altri governi seguono l'esempio",
        "horizon": "Breve termine (1-3 giorni) per il panic, poi recupero in 1-2 settimane",
        "action": "ATTENDI il panic sell, poi accumula gradualmente",
        "affected": "crypto in generale, BTC in primis",
    },
    "regulation": {
        "analysis": "Nuove regolamentazioni possono essere positive (chiarezza normativa) o negative (restrizioni). La reazione iniziale è spesso negativa, ma la regolamentazione chiara attrae investitori istituzionali nel medio termine.",
        "opportunity": "Se la regolamentazione è chiara e non restrittiva, è bullish per gli istituzionali",
        "risk": "Regole troppo restrittive possono limitare l'adozione",
        "horizon": "Medio termine (2-4 settimane) per assorbire l'impatto",
        "action": "Valuta il tono della regolamentazione prima di agire",
        "affected": "tutto il mercato crypto, exchange token",
    },
    # --- ETF / ISTITUZIONALI ---
    "etf": {
        "analysis": "Le notizie sugli ETF crypto sono tra le più impattanti. Un'approvazione ETF porta flussi istituzionali massicci (+5/15% in settimane). Un rigetto causa sell-off immediato (-3/8%).",
        "opportunity": "Se approvato: COMPRA immediatamente, il prezzo sale rapidamente. Se rigettato: attendi il bottom per accumulare",
        "risk": "Sell the news dopo l'euforia iniziale dell'approvazione",
        "horizon": "Immediato (ore) per la reazione, poi trend per settimane",
        "action": "Entra subito se approvazione, attendi se rigetto",
        "affected": "BTC, ETH (per ETF spot), tutto il mercato crypto",
    },
    "blackrock": {
        "analysis": "BlackRock è il più grande asset manager al mondo ($10T+ AUM). Ogni loro mossa nel crypto è un segnale fortissimo di adozione istituzionale e porta enormi flussi di capitale.",
        "opportunity": "Qualsiasi coinvolgimento BlackRock nel crypto è estremamente bullish",
        "risk": "Possibile sell the news se già prezzato dal mercato",
        "horizon": "Medio-lungo termine (settimane-mesi)",
        "action": "COMPRA gli asset coinvolti, accumula su ogni dip",
        "affected": "BTC, ETH, RWA/tokenizzazione, ONDO",
    },
    # --- MACRO ---
    "fed": {
        "analysis": "Le decisioni della Federal Reserve sui tassi impattano TUTTI i mercati. Taglio tassi = bullish per risk assets (crypto, azioni). Rialzo tassi = bearish, capitali verso bond/USD.",
        "opportunity": "Taglio tassi: accumula BTC, ETH, azioni tech. Rialzo: sposta su stablecoin e oro",
        "risk": "La reazione può essere opposta alle attese (buy the rumor, sell the news)",
        "horizon": "Immediato (ore) per la reazione, poi trend per settimane",
        "action": "Segui la direzione del mercato nelle prime 2 ore dopo l'annuncio",
        "affected": "BTC, ETH, ORO, S&P500, NASDAQ, USD",
    },
    "inflation": {
        "analysis": "Dati inflazione sopra le attese = bearish (la Fed potrebbe alzare i tassi). Sotto le attese = bullish (possibili tagli). L'inflazione è il driver principale delle politiche monetarie.",
        "opportunity": "Inflazione in calo: accumula crypto e azioni. In salita: hedge con oro e stablecoin",
        "risk": "Dati possono essere rivisti, reazione iniziale può invertirsi",
        "horizon": "Breve termine (1-3 giorni) per la reazione, poi dipende dal trend",
        "action": "Confronta dato attuale vs previsione per decidere la direzione",
        "affected": "BTC, ORO, S&P500, USD, bond",
    },
    "recession": {
        "analysis": "Segnali di recessione causano flight to safety: vendita di risk assets (crypto, azioni) e acquisto di beni rifugio (oro, bond, USD). BTC può comportarsi come risk asset O come oro digitale.",
        "opportunity": "Accumulo aggressivo di BTC e ORO durante il panico. Le recessioni creano le migliori opportunità di acquisto",
        "risk": "Il mercato può scendere molto più del previsto. Usa DCA, non all-in",
        "horizon": "Medio-lungo termine (mesi)",
        "action": "Riduci leva, aumenta stablecoin, DCA su BTC e ORO",
        "affected": "tutto il mercato, BTC, ORO, S&P500",
    },
    "tariff": {
        "analysis": "I dazi commerciali creano incertezza economica e tensioni geopolitiche. Impatto negativo su azioni e commercio globale. Crypto può beneficiare come hedge contro l'instabilità.",
        "opportunity": "BTC come hedge geopolitico, ORO come bene rifugio tradizionale",
        "risk": "Escalation delle tensioni commerciali può causare sell-off generalizzato",
        "horizon": "Breve-medio termine (giorni-settimane)",
        "action": "Aumenta esposizione a BTC e ORO, riduci azioni",
        "affected": "ORO, BTC, azioni, valute dei paesi coinvolti",
    },
    "war": {
        "analysis": "Conflitti geopolitici causano volatilità estrema. Flight to safety verso oro, USD, bond. Crypto volatile ma BTC può fungere da hedge. Petrolio sale per rischio supply.",
        "opportunity": "ORO e BTC come hedge, petrolio se conflitto in Medio Oriente",
        "risk": "Volatilità estrema, possibili flash crash",
        "horizon": "Immediato per il panico, poi dipende dall'evoluzione",
        "action": "Riduci rischio, aumenta cash e oro, piccola posizione BTC",
        "affected": "ORO, PETROLIO, BTC, USD, azioni",
    },
    # --- CRYPTO SPECIFICI ---
    "hack": {
        "analysis": "Un hack di un exchange o protocollo DeFi causa panic sell immediato sull'asset coinvolto e spesso contagio su tutto il mercato crypto (-2/5% generale). La fiducia si recupera in 1-4 settimane.",
        "opportunity": "Accumulo dopo il panic sell se il protocollo è solido e rimborsa gli utenti",
        "risk": "L'exchange/protocollo potrebbe non recuperare. Mai comprare il token hackerato subito",
        "horizon": "Breve termine (1-7 giorni) per il panic, poi valuta",
        "action": "ATTENDI 24-48h, poi valuta se accumulare il dip su BTC/ETH",
        "affected": "token/exchange hackerato, BTC, ETH per contagio",
    },
    "halving": {
        "analysis": "L'halving di Bitcoin dimezza la ricompensa dei miner, riducendo l'offerta. Storicamente, il prezzo sale del 200-500% nei 12-18 mesi successivi. È l'evento più bullish del ciclo crypto.",
        "opportunity": "ACCUMULA BTC prima e durante l'halving. Storicamente il miglior investimento possibile",
        "risk": "Sell the news nel breve termine, ma il trend è sempre stato rialzista",
        "horizon": "Lungo termine (6-18 mesi)",
        "action": "DCA aggressivo su BTC, poi ETH e altcoin seguiranno",
        "affected": "BTC in primis, poi tutto il mercato crypto",
    },
    "crash": {
        "analysis": "Un crash di mercato (-10% o più) crea panico ma anche le migliori opportunità di acquisto. Storicamente, chi compra durante i crash ottiene i rendimenti migliori nel medio-lungo termine.",
        "opportunity": "ACCUMULO AGGRESSIVO se hai liquidità. Le fortune si fanno comprando nel sangue",
        "risk": "Il mercato può scendere ulteriormente. Usa DCA su 3-5 giorni, non all-in",
        "horizon": "Breve per il panic (1-3 giorni), recupero in 2-8 settimane",
        "action": "DCA immediato su BTC e ETH, aumenta gradualmente",
        "affected": "tutto il mercato",
    },
    "surge": {
        "analysis": "Un rally forte (+5% o più) indica momentum rialzista. Attenzione al FOMO: entrare dopo un pump è rischioso. Meglio aspettare un ritracciamento del 2-3% per entrare.",
        "opportunity": "Se il rally è supportato da volumi alti e notizie fondamentali, il trend può continuare",
        "risk": "Bull trap: il prezzo può ritracciare bruscamente dopo il pump iniziale",
        "horizon": "Breve termine (ore-giorni)",
        "action": "NON inseguire il pump. Attendi ritracciamento del 2-3% per entrare",
        "affected": "asset specifico del rally",
    },
    "partnership": {
        "analysis": "Le partnership strategiche indicano adozione crescente e possono portare a rialzi del 5-20% nel breve termine. L'impatto dipende dalla dimensione del partner.",
        "opportunity": "Compra subito se il partner è un big player (Fortune 500, governo, banca)",
        "risk": "Molte partnership sono solo marketing e non portano valore reale",
        "horizon": "Breve-medio termine (giorni-settimane)",
        "action": "Valuta la credibilità del partner prima di investire",
        "affected": "token/progetto specifico della partnership",
    },
    "listing": {
        "analysis": "Il listing su un exchange major (Binance, Coinbase) porta visibilità e liquidità. Tipicamente +10-50% nelle prime 24h, poi ritracciamento. Il delist è l'opposto: -20/50%.",
        "opportunity": "Compra PRIMA del listing se possibile, vendi nelle prime ore dopo il listing",
        "risk": "Pump and dump: molti token crollano dopo il listing iniziale",
        "horizon": "Immediato (ore) per il pump, poi ritracciamento in 1-3 giorni",
        "action": "Se già listato: attendi il ritracciamento. Se pre-listing: valuta entry",
        "affected": "token specifico del listing",
    },
    "tokeniz": {
        "analysis": "La tokenizzazione di asset reali (RWA) è uno dei trend più forti del ciclo. BlackRock, Securitize e altri big player stanno entrando. Il mercato RWA potrebbe raggiungere $16T entro il 2030.",
        "opportunity": "ONDO, RWA token, e piattaforme di tokenizzazione sono i principali beneficiari",
        "risk": "Regolamentazione ancora incerta, possibili ritardi nell'adozione",
        "horizon": "Medio-lungo termine (mesi-anni)",
        "action": "Accumula ONDO e token RWA su ogni dip. Posizione a lungo termine",
        "affected": "ONDO, RWA, SECURITIZE, ETH (infrastruttura)",
    },
    # --- DEFAULT ---
    "default": {
        "analysis": "Notizia di impatto moderato sul mercato. Monitora l'evoluzione nelle prossime ore per capire se il mercato reagisce.",
        "opportunity": "Mantieni le posizioni attuali e osserva la reazione del mercato",
        "risk": "Impatto limitato, ma potrebbe essere il primo segnale di un trend",
        "horizon": "Breve termine (ore-giorni)",
        "action": "Osserva e attendi conferme prima di agire",
        "affected": "dipende dal contesto specifico",
    },
}

def analyze_news_impact(title, desc, assets, crypto_prices, commodities):
    """Genera analisi impatto dettagliata per una notizia."""
    text = f"{title} {desc}".lower()
    
    # Trova lo scenario più rilevante
    scenario = None
    matched_key = "default"
    priority_order = ["etf", "blackrock", "fed", "halving", "crash", "hack", "ban",
                      "war", "recession", "tariff", "inflation", "regulation",
                      "surge", "tokeniz", "partnership", "listing"]
    
    for key in priority_order:
        if key in text:
            scenario = IMPACT_SCENARIOS[key]
            matched_key = key
            break
    
    if not scenario:
        scenario = IMPACT_SCENARIOS["default"]
    
    # Calcola impatto stimato sui prezzi
    price_impact = ""
    if assets and crypto_prices:
        impacts = []
        for asset in assets[:3]:
            p_data = crypto_prices.get(asset) or commodities.get(asset)
            if p_data and p_data.get("price", 0) > 0:
                p = p_data["price"]
                # Stima range di movimento basato sul tipo di notizia
                if matched_key in ("etf", "blackrock", "halving"):
                    low, high = -3, 15
                elif matched_key in ("crash", "hack", "ban"):
                    low, high = -15, -3
                elif matched_key in ("fed", "inflation", "recession"):
                    low, high = -8, 8
                elif matched_key in ("war", "tariff"):
                    low, high = -10, 5
                elif matched_key in ("surge", "partnership", "listing"):
                    low, high = -2, 20
                elif matched_key == "tokeniz":
                    low, high = 0, 10
                else:
                    low, high = -3, 3
                
                p_low = p * (1 + low/100)
                p_high = p * (1 + high/100)
                impacts.append(f"  📉 {asset}: ${p_low:,.0f} ({low:+d}%) → ${p_high:,.0f} ({high:+d}%)")
        
        if impacts:
            price_impact = "\n".join(impacts)
    
    # Suggerimento allocazione specifico
    alloc_tip = ""
    if matched_key in ("etf", "blackrock", "halving", "surge", "partnership", "tokeniz"):
        alloc_tip = "💰 Aumenta esposizione: +10/20% su asset coinvolti"
    elif matched_key in ("crash", "hack", "ban"):
        alloc_tip = "💰 Accumula il dip: DCA su BTC/ETH dopo -5% o più"
    elif matched_key in ("war", "recession"):
        alloc_tip = "💰 Modalità difensiva: 40% stablecoin, 30% ORO, 20% BTC, 10% ETH"
    elif matched_key in ("fed", "inflation", "tariff"):
        alloc_tip = "💰 Attendi la reazione del mercato (2-4h) prima di allocare"
    elif matched_key == "regulation":
        alloc_tip = "💰 Se positiva: accumula. Se negativa: riduci del 10-15%"
    else:
        alloc_tip = "💰 Mantieni allocazione attuale, monitora evoluzione"
    
    # Costruisci messaggio
    msg = f"\n🧠 <b>ANALISI IMPATTO</b>\n{'━' * 28}\n"
    msg += f"📋 {escape_html(scenario['analysis'])}\n\n"
    
    if price_impact:
        msg += f"📊 <b>Range prezzo stimato:</b>\n{price_impact}\n\n"
    
    msg += f"✅ <b>Opportunità:</b> {escape_html(scenario['opportunity'])}\n"
    msg += f"⚠️ <b>Rischio:</b> {escape_html(scenario['risk'])}\n"
    msg += f"⏱️ <b>Orizzonte:</b> {scenario['horizon']}\n"
    msg += f"🎯 <b>Azione:</b> {escape_html(scenario['action'])}\n"
    msg += f"{alloc_tip}\n"
    
    return msg

# ---------------------------------------------------------------------------
# GENERAZIONE STRATEGIA
# ---------------------------------------------------------------------------
def generate_news_strategy(title, desc, impact, assets, crypto_prices, commodities, ta_cache):
    text = f"{title} {desc}".lower()
    bull_kw = ["approval", "launch", "partner", "surge", "rally", "bullish", "record",
               "ath", "cut", "stimulus", "adopt", "soar", "gain", "positive", "growth"]
    bear_kw = ["reject", "ban", "crash", "hack", "dump", "bearish", "recession",
               "default", "collapse", "plunge", "negative", "loss", "decline",
               "sanction", "war", "tariff", "hike"]
    bull = sum(1 for w in bull_kw if w in text)
    bear = sum(1 for w in bear_kw if w in text)

    if bull > bear:
        sentiment, direction, emoji = "RIALZISTA", "BUY", "🟢"
    elif bear > bull:
        sentiment, direction, emoji = "RIBASSISTA", "SELL", "🔴"
    else:
        sentiment, direction, emoji = "NEUTRO", "ATTENDI", "⚪"

    s = f"\n{'━' * 28}\n"
    s += f"{emoji} <b>SENTIMENT: {sentiment}</b>\n"

    if direction == "ATTENDI":
        s += f"⏳ Notizia ambigua, attendi conferma dal mercato\n"
        return s, sentiment

    for asset in assets[:3]:
        price_data = crypto_prices.get(asset) or commodities.get(asset)
        if not price_data:
            continue
        p = price_data.get("price", 0)
        ch = price_data.get("change_24h", 0)
        if p == 0:
            continue

        s += f"\n{'━' * 28}\n"
        if direction == "BUY":
            s += f"🟢🟢🟢 <b>COMPRA {asset}</b> 🟢🟢🟢\n"
        else:
            s += f"🔴🔴🔴 <b>VENDI {asset}</b> 🔴🔴🔴\n"
        s += f"💰 Prezzo: ${p:,.2f} ({ch:+.1f}% 24h)\n"

        coin_id = None
        for cid, sym in CRYPTO_SYMBOLS.items():
            if sym == asset:
                coin_id = cid
                break
        ta = ta_cache.get(coin_id) if coin_id else None

        if ta:
            rsi = ta["rsi"]
            s += f"📊 RSI(14): {rsi}"
            if rsi < 30: s += " (IPERVENDUTO ✅)"
            elif rsi > 70: s += " (IPERCOMPRATO ⚠️)"
            s += f"\n"
            s += f"📈 Trend 24h: {ta['trend_24h']} | 7d: {ta['trend_7d']}\n"
            s += f"🛡️ Supporto: ${ta['support']:,.2f} | Resistenza: ${ta['resistance']:,.2f}\n"

            confirmations = 0
            if direction == "BUY":
                if rsi < 40: confirmations += 1
                if ta["above_ma24"]: confirmations += 1
                if ta["trend_24h"] == "UP": confirmations += 1
                if p < ta["support"] * 1.05: confirmations += 1
            else:
                if rsi > 60: confirmations += 1
                if not ta["above_ma24"]: confirmations += 1
                if ta["trend_24h"] == "DOWN": confirmations += 1
                if p > ta["resistance"] * 0.95: confirmations += 1

            s += f"✅ Conferme tecniche: {confirmations}/4\n"
            if confirmations < 2 and impact < 4:
                s += f"⚠️ <b>CAUTELA:</b> poche conferme tecniche\n"
            if direction == "BUY" and rsi > 70:
                s += f"🚫 <b>TRAP WARNING:</b> RSI ipercomprato, rischio bull trap!\n"
            elif direction == "SELL" and rsi < 30:
                s += f"🚫 <b>TRAP WARNING:</b> RSI ipervenduto, rischio bear trap!\n"

        # Strategia SPOT
        s += f"\n📦 <b>SPOT ({asset}):</b>\n"
        if direction == "BUY":
            s += f"   Compra a ${p:,.2f}\n"
            s += f"   Importo: 10-30€ (accumulo graduale)\n"
        else:
            s += f"   Vendi/riduci posizione a ${p:,.2f}\n"
            s += f"   Proteggi profitti\n"

        # Strategia SCALPING/DAYTRADING (solo se TA disponibile)
        if ta and p > 0:
            if direction == "BUY":
                sl_pct = 0.4
                tp_pct = sl_pct * MIN_RR
                entry = p
                sl = entry * (1 - sl_pct / 100)
                tp = entry * (1 + tp_pct / 100)
                rr = tp_pct / sl_pct
            else:
                sl_pct = 0.4
                tp_pct = sl_pct * MIN_RR
                entry = p
                sl = entry * (1 + sl_pct / 100)
                tp = entry * (1 - tp_pct / 100)
                rr = tp_pct / sl_pct

            leva = 5
            capitale = 100
            pos_size = capitale * leva
            profit_tp = pos_size * tp_pct / 100
            loss_sl = pos_size * sl_pct / 100

            s += f"\n⚡ <b>SCALPING {'LONG' if direction == 'BUY' else 'SHORT'} ({asset}):</b>\n"
            s += f"   Entry: ${entry:,.2f}\n"
            s += f"   Take Profit: ${tp:,.2f} (+{tp_pct:.1f}%)\n"
            s += f"   Stop Loss: ${sl:,.2f} (-{sl_pct:.1f}%)\n"
            s += f"   R:R: 1:{rr:.0f} | Leva: {leva}x\n"
            s += f"   TF: 1-4 ore\n"
            s += f"   Con 100€ (leva {leva}x): +${profit_tp:.0f} TP / -${loss_sl:.0f} SL\n"

            # DAYTRADING
            now_it = now_italy()
            hours_left = max(1, 24 - now_it.hour)
            dt_sl_pct = 0.5
            dt_tp1_pct = dt_sl_pct * MIN_RR
            dt_tp2_pct = dt_tp1_pct * 1.5

            if direction == "BUY":
                dt_entry = p
                dt_sl = dt_entry * (1 - dt_sl_pct / 100)
                dt_tp1 = dt_entry * (1 + dt_tp1_pct / 100)
                dt_tp2 = dt_entry * (1 + dt_tp2_pct / 100)
                close_condition = f"Se alle 00:00 il prezzo chiude SOPRA ${dt_entry:,.2f} → mantieni"
                invalidation = f"NON entrare se {asset} scende sotto ${dt_sl:,.2f}"
            else:
                dt_entry = p
                dt_sl = dt_entry * (1 + dt_sl_pct / 100)
                dt_tp1 = dt_entry * (1 - dt_tp1_pct / 100)
                dt_tp2 = dt_entry * (1 - dt_tp2_pct / 100)
                close_condition = f"Se alle 00:00 il prezzo chiude SOTTO ${dt_entry:,.2f} → mantieni"
                invalidation = f"NON entrare se {asset} sale sopra ${dt_sl:,.2f}"

            dt_profit = capitale * leva * dt_tp1_pct / 100
            dt_loss = capitale * leva * dt_sl_pct / 100

            s += f"\n📊 <b>DAYTRADING {'LONG' if direction == 'BUY' else 'SHORT'} ({asset}):</b>\n"
            s += f"   Entry: ${dt_entry:,.2f}\n"
            s += f"   TP1: ${dt_tp1:,.2f} (+{dt_tp1_pct:.1f}%)\n"
            s += f"   TP2: ${dt_tp2:,.2f} (+{dt_tp2_pct:.1f}%)\n"
            s += f"   SL: ${dt_sl:,.2f} (-{dt_sl_pct:.1f}%)\n"
            s += f"   R:R: 1:{MIN_RR:.0f} | Leva: {leva}x\n"
            s += f"   Durata: max {hours_left}h (chiudi entro 00:00)\n"
            s += f"   Con 100€ (leva {leva}x): +${dt_profit:.0f} TP / -${dt_loss:.0f} SL\n"
            s += f"   📋 {close_condition}\n"
            s += f"   🚫 {invalidation}\n"

    return s, sentiment

# ---------------------------------------------------------------------------
# SESSIONI DI MERCATO
# ---------------------------------------------------------------------------
def check_sessions(state):
    now = datetime.now(timezone.utc)
    h_utc = now.hour
    m = now.minute
    alerts = []
    today = now.strftime("%Y%m%d")

    for key, ses in MARKET_SESSIONS.items():
        oh = ses["open_utc"]
        ch_h = ses["close_utc"]
        name = ses["name"]
        em = ses["emoji"]
        assets = ses["assets"]

        okey = f"open_{key}_{today}"
        if h_utc == oh and m < 5 and okey not in state.get("sessions", []):
            open_it = utc_to_italy(oh)
            close_it = utc_to_italy(ch_h)
            msg = (
                f"🔔 <b>APERTURA MERCATO</b>\n{'━' * 28}\n"
                f"{em} <b>Sessione {name} APERTA</b>\n"
                f"⏰ Orario: {open_it} - {close_it} (ora italiana)\n"
                f"💎 Asset principali: {assets}\n\n"
            )
            if key == "LONDRA":
                msg += "💡 Alta volatilità su EUR, GBP, ORO, ARGENTO\n"
                msg += "💡 Overlap con Tokyo = movimenti forti\n"
            elif key == "NEW_YORK":
                msg += "💡 Massima volatilità globale (overlap con Londra)\n"
                msg += "💡 Dati USA: NFP, CPI, Fed speakers\n"
                msg += "💡 Movimenti forti su USD, ORO, BTC, S&P500\n"
            elif key == "TOKYO":
                msg += "💡 Movimenti su JPY, crypto spesso attive\n"
            elif key == "SYDNEY":
                msg += "💡 Inizio settimana di trading\n"
                msg += "💡 Bassa liquidità, spread più ampi\n"
            alerts.append(msg)
            state.setdefault("sessions", []).append(okey)

        ckey = f"close_{key}_{today}"
        if h_utc == ch_h and m < 5 and ckey not in state.get("sessions", []):
            msg = f"🔕 {em} <b>Sessione {name} CHIUSA</b>\n"
            alerts.append(msg)
            state.setdefault("sessions", []).append(ckey)

    return alerts

# ---------------------------------------------------------------------------
# SEGNALI TRADING (PRICE ALERTS)
# ---------------------------------------------------------------------------
def check_price_signals(crypto_prices, commodities, price_history, ta_cache, state):
    signals = []
    all_prices = {}
    all_prices.update(crypto_prices)
    all_prices.update(commodities)

    for asset, data in all_prices.items():
        p = data.get("price", 0)
        ch = data.get("change_24h", 0)
        if p == 0:
            continue

        prev = price_history.get(asset, {}).get("price", p)
        if prev == 0:
            prev = p
        move_pct = abs((p - prev) / prev * 100) if prev else 0

        if move_pct >= 3:
            direction = "UP" if p > prev else "DOWN"
            coin_id = None
            for cid, sym in CRYPTO_SYMBOLS.items():
                if sym == asset:
                    coin_id = cid
                    break
            ta = ta_cache.get(coin_id) if coin_id else None

            if direction == "UP":
                action = "BUY" if ta and ta["rsi"] < 65 else "CAUTELA"
                emoji = "🟢" if action == "BUY" else "🟡"
            else:
                action = "SELL" if ta and ta["rsi"] > 35 else "CAUTELA"
                emoji = "🔴" if action == "SELL" else "🟡"

            msg = f"⚡ <b>MOVIMENTO {asset}</b>\n{'━' * 28}\n"
            msg += f"{'📈' if direction == 'UP' else '📉'} <b>{asset}: ${p:,.2f}</b> ({ch:+.1f}% 24h)\n"
            msg += f"🔄 Variazione: {'+' if direction == 'UP' else ''}{(p-prev)/prev*100:.1f}% dall'ultimo check\n\n"

            if ta:
                msg += f"📊 RSI: {ta['rsi']}"
                if ta['rsi'] < 30: msg += " (IPERVENDUTO)"
                elif ta['rsi'] > 70: msg += " (IPERCOMPRATO)"
                msg += f"\n"
                msg += f"📈 Trend: 24h {ta['trend_24h']} | 7d {ta['trend_7d']}\n"
                msg += f"🛡️ S: ${ta['support']:,.2f} | R: ${ta['resistance']:,.2f}\n\n"

                confirmations = 0
                if action == "BUY":
                    if ta["rsi"] < 40: confirmations += 1
                    if ta["above_ma24"]: confirmations += 1
                    if ta["trend_24h"] == "UP": confirmations += 1
                else:
                    if ta["rsi"] > 60: confirmations += 1
                    if not ta["above_ma24"]: confirmations += 1
                    if ta["trend_24h"] == "DOWN": confirmations += 1
                msg += f"✅ Conferme: {confirmations}/3\n"

            if action in ("BUY", "SELL") and ta and p > 0:
                sl_pct = 0.4
                tp_pct = sl_pct * MIN_RR
                leva = 5
                profit = 100 * leva * tp_pct / 100
                loss = 100 * leva * sl_pct / 100

                if action == "BUY":
                    tp = p * (1 + tp_pct / 100)
                    sl = p * (1 - sl_pct / 100)
                    msg += f"\n🟢🟢🟢 <b>COMPRA {asset}</b> 🟢🟢🟢\n"
                else:
                    tp = p * (1 - tp_pct / 100)
                    sl = p * (1 + sl_pct / 100)
                    msg += f"\n🔴🔴🔴 <b>VENDI {asset}</b> 🔴🔴🔴\n"

                msg += f"⚡ Entry: ${p:,.2f}\n"
                msg += f"🎯 TP: ${tp:,.2f} (+{tp_pct:.1f}%)\n"
                msg += f"🛑 SL: ${sl:,.2f} (-{sl_pct:.1f}%)\n"
                msg += f"📊 R:R: 1:{MIN_RR:.0f} | Leva: {leva}x\n"
                msg += f"💰 Con 100€: +${profit:.0f} TP / -${loss:.0f} SL\n"

                if action == "BUY" and ta["rsi"] > 70:
                    msg += f"\n🚫 <b>TRAP WARNING:</b> RSI ipercomprato!\n"
                elif action == "SELL" and ta["rsi"] < 30:
                    msg += f"\n🚫 <b>TRAP WARNING:</b> RSI ipervenduto!\n"
            elif action == "CAUTELA":
                msg += f"\n⚠️ <b>CAUTELA:</b> movimento forte ma segnali tecnici misti. Attendi conferma.\n"

            msg += f"\n⏰ {format_time_it()} ora italiana\n"
            signals.append(msg)

        price_history[asset] = {"price": p, "time": time.time()}

    return signals

# ---------------------------------------------------------------------------
# FORMATO NOTIZIE
# ---------------------------------------------------------------------------
def format_calendar(event, prices):
    impact_str = "🔴 ALTO" if "high" in str(event.get("impact","")).lower() else \
                 "🟡 MEDIO" if "medium" in str(event.get("impact","")).lower() else "🟢 BASSO"
    title_it = translate(event.get("title", ""))
    currency = event.get("currency", "")
    time_utc = event.get("time_utc", "")

    time_it = ""
    if time_utc and ":" in time_utc:
        try:
            parts = time_utc.split(":")
            h_utc = int(parts[0])
            h_it = (h_utc + 2) % 24
            time_it = f"{h_it:02d}:{parts[1]}"
        except:
            time_it = time_utc

    msg = f"📅 <b>EVENTO ECONOMICO</b>\n{'━' * 28}\n"
    msg += f"📊 <b>{escape_html(title_it)}</b>\n"
    msg += f"💱 {escape_html(currency)} | Impatto: {impact_str}\n"
    if time_it:
        msg += f"⏰ Ore {time_it} (ora italiana)\n"

    actual = event.get("actual", "")
    forecast = event.get("forecast", "")
    previous = event.get("previous", "")
    if actual:
        msg += f"📈 Attuale: <b>{escape_html(actual)}</b>"
        if forecast: msg += f" (Previsto: {escape_html(forecast)})"
        if previous: msg += f" (Prec: {escape_html(previous)})"
        msg += "\n"
        try:
            a = float(re.sub(r'[^\d.\-]', '', actual))
            f_val = float(re.sub(r'[^\d.\-]', '', forecast))
            if a > f_val:
                msg += f"✅ <b>SOPRA LE ATTESE</b> → Possibile impatto positivo su {currency}\n"
            elif a < f_val:
                msg += f"❌ <b>SOTTO LE ATTESE</b> → Possibile impatto negativo su {currency}\n"
        except:
            pass
    elif forecast:
        msg += f"📊 Previsto: {escape_html(forecast)}"
        if previous: msg += f" | Prec: {escape_html(previous)}"
        msg += "\n"

    assets = find_affected_assets(event["title"])
    if "high" in str(event.get("impact","")).lower() and assets:
        msg += f"\n💎 Asset impattati: {', '.join(assets)}\n"
        msg += f"⚠️ Aspettati volatilità elevata!\n"

    msg += f"\n📰 ForexFactory | ⏰ {format_time_it()}\n"
    return msg

def format_news(item, score, label, assets, strategy, impact_analysis=""):
    title_it = translate(item.get("title", ""))
    desc_it = translate(re.sub(r'<[^>]+>', '', item.get("description", ""))[:400])
    link = item.get("link", "")
    source = item.get("source", "")
    ntype = item.get("type", "")

    type_em = {"CRYPTO": "🪙", "COMMODITIES": "🥇", "MACRO": "🏦", "FOREX": "💱"}.get(ntype, "📰")

    if score >= 4:
        header = f"🚨🚨🚨 <b>ALERT CRITICO</b> 🚨🚨🚨\n{'━' * 28}\n"
    elif score >= 3:
        header = f"🔔🔔 <b>ALERT IMPORTANTE</b> 🔔🔔\n{'━' * 28}\n"
    elif score >= 2:
        header = f"🔔 <b>NOTIZIA</b>\n{'━' * 28}\n"
    else:
        header = f"📰 <b>NEWS</b>\n{'━' * 28}\n"

    msg = header
    msg += f"{type_em} <b>{escape_html(title_it)}</b>\n\n"
    if desc_it:
        msg += f"📝 {escape_html(desc_it)}\n\n"
    msg += f"📊 Impatto: {label}\n"
    if assets:
        msg += f"💎 Asset: {', '.join(assets)}\n"
    msg += strategy
    if impact_analysis:
        msg += impact_analysis
    if link:
        msg += f"\n🔗 <a href=\"{escape_html(link)}\">Fonte originale</a>\n"
    msg += f"📰 {escape_html(source)} | ⏰ {format_time_it()}\n"
    return msg

# ---------------------------------------------------------------------------
# FEAR & GREED ALERT
# ---------------------------------------------------------------------------
def check_fng_alert(state):
    fng = get_fear_greed()
    if not fng:
        return None
    val = fng["value"]
    prev = fng["previous"]
    label = fng["label"]
    today = now_italy().strftime("%Y%m%d")
    key = f"fng_{today}"

    prev_label = state.get("last_fng_label", "")
    if key in state.get("fng_sent", []):
        return None

    if val <= 20 or val >= 80 or label != prev_label:
        state["last_fng_label"] = label
        state.setdefault("fng_sent", []).append(key)

        if val <= 20:
            emoji, action, tip = "😱", "🟢 ZONA DI ACCUMULO STORICA", "Extreme Fear = opportunità di acquisto storicamente profittevole"
        elif val <= 40:
            emoji, action, tip = "😰", "🟢 Possibile accumulo graduale", "Paura = prezzi potenzialmente scontati"
        elif val <= 60:
            emoji, action, tip = "😐", "⚪ Mercato neutro, attendi segnali", "Nessuna emozione dominante"
        elif val <= 80:
            emoji, action, tip = "😀", "🟡 Cautela, riduci rischio", "Greed = mercato potenzialmente sopravvalutato"
        else:
            emoji, action, tip = "🤑", "🔴 ATTENZIONE: rischio correzione", "Extreme Greed = storicamente precede correzioni"

        msg = f"🧠 <b>FEAR &amp; GREED INDEX</b>\n{'━' * 28}\n"
        msg += f"{emoji} <b>Valore: {val}/100 ({label})</b>\n"
        msg += f"📊 Ieri: {prev}/100\n"
        msg += f"📈 Variazione: {val - prev:+d}\n\n"
        msg += f"{action}\n"
        msg += f"💡 {tip}\n"
        msg += f"\n⏰ {format_time_it()} ora italiana\n"
        return msg
    return None

# ---------------------------------------------------------------------------
# ALLOCAZIONE CONSIGLIATA
# ---------------------------------------------------------------------------
def generate_allocation(sentiments, crypto_prices, commodities, fng=None):
    """Genera messaggio di allocazione consigliata basato sul sentiment aggregato."""
    bull_count = sentiments.count("RIALZISTA")
    bear_count = sentiments.count("RIBASSISTA")
    neutral_count = sentiments.count("NEUTRO")
    total = len(sentiments) if sentiments else 1

    # Determina sentiment aggregato
    if bull_count > bear_count and bull_count > neutral_count:
        market_sentiment = "RIALZISTA"
        strategy = "AGGRESSIVA"
        risk = "MEDIO-ALTO"
        horizon = "Breve-Medio termine (1-4 settimane)"
        alloc = [
            ("🟠", "BTC", 35, "accumulo"),
            ("🔵", "ETH", 20, "accumulo"),
            ("🟣", "SOL", 15, "trading attivo"),
            ("🥇", "ORO", 10, "hedge"),
            ("💵", "Stablecoin", 10, "riserva liquidità"),
            ("🔶", "ALTCOIN (ONDO/AVAX/LINK)", 10, "speculazione"),
        ]
    elif bear_count > bull_count and bear_count > neutral_count:
        market_sentiment = "RIBASSISTA"
        strategy = "DIFENSIVA"
        risk = "BASSO"
        horizon = "Breve termine (1-2 settimane), poi rivaluta"
        alloc = [
            ("💵", "Stablecoin (USDT/USDC)", 40, "protezione capitale"),
            ("🥇", "ORO", 25, "bene rifugio"),
            ("🟠", "BTC", 20, "accumulo DCA"),
            ("🔵", "ETH", 10, "accumulo DCA"),
            ("🟣", "SOL", 5, "piccola posizione"),
        ]
    else:
        market_sentiment = "NEUTRO"
        strategy = "MODERATA"
        risk = "MEDIO"
        horizon = "Medio termine (2-4 settimane)"
        alloc = [
            ("🟠", "BTC", 30, "accumulo"),
            ("🔵", "ETH", 20, "accumulo"),
            ("🥇", "ORO", 15, "hedge"),
            ("💵", "Stablecoin", 15, "riserva"),
            ("🟣", "SOL", 10, "trading"),
            ("🔶", "ALTCOIN", 10, "diversificazione"),
        ]

    # Aggiusta in base a Fear & Greed
    fng_data = fng or get_fear_greed()
    fng_note = ""
    if fng_data:
        val = fng_data["value"]
        if val <= 25:
            fng_note = f"😱 Fear &amp; Greed: {val}/100 (Extreme Fear) → OTTIMO momento per accumulare!"
        elif val <= 40:
            fng_note = f"😰 Fear &amp; Greed: {val}/100 (Fear) → Buon momento per comprare gradualmente"
        elif val >= 75:
            fng_note = f"🤑 Fear &amp; Greed: {val}/100 (Extreme Greed) → CAUTELA, riduci esposizione"
        elif val >= 60:
            fng_note = f"😀 Fear &amp; Greed: {val}/100 (Greed) → Attenzione, non inseguire i pump"

    # Emoji sentiment
    sent_emoji = {"RIALZISTA": "🟢", "RIBASSISTA": "🔴", "NEUTRO": "⚪"}.get(market_sentiment, "⚪")

    msg = f"💼 <b>ALLOCAZIONE CONSIGLIATA</b>\n{'━' * 28}\n"
    msg += f"{sent_emoji} Sentiment mercato: <b>{market_sentiment}</b>\n"
    msg += f"🎯 Strategia: <b>{strategy}</b>\n\n"
    msg += f"💎 <b>Portafoglio suggerito:</b>\n"

    capitale = 100
    for emoji, asset, pct, note in alloc:
        amount = capitale * pct / 100
        msg += f"  {emoji} {asset}: {pct}% ({note}) → {amount:.0f}€\n"

    msg += f"\n⏱️ Orizzonte: {horizon}\n"
    msg += f"⚠️ Rischio: {risk}\n"
    msg += f"💰 Calcolato su capitale: {capitale}€\n"

    if fng_note:
        msg += f"\n{fng_note}\n"

    # Prezzi attuali per contesto
    btc_p = crypto_prices.get("BTC", {}).get("price", 0)
    eth_p = crypto_prices.get("ETH", {}).get("price", 0)
    sol_p = crypto_prices.get("SOL", {}).get("price", 0)
    gold_p = commodities.get("ORO", {}).get("price", 0)
    if btc_p:
        msg += f"\n📊 <b>Prezzi ora:</b>\n"
        msg += f"  🟠 BTC: ${btc_p:,.0f}\n"
        if eth_p: msg += f"  🔵 ETH: ${eth_p:,.0f}\n"
        if sol_p: msg += f"  🟣 SOL: ${sol_p:,.0f}\n"
        if gold_p: msg += f"  🥇 ORO: ${gold_p:,.0f}\n"

    msg += f"\n{'━' * 28}\n"
    msg += f"⏰ {format_time_it()} ora italiana\n"
    msg += f"⚠️ Non è consulenza finanziaria. DYOR!\n"
    return msg

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    log.info(f"TradingAlertBot v{VERSION} avviato")

    state = load_json(STATE_FILE, {"welcome_sent": False, "sessions": [], "fng_sent": []})
    seen = load_json(SEEN_FILE, {"hashes": [], "last_clean": ""})
    price_history = load_json(PRICE_HISTORY_FILE, {})

    # Benvenuto (solo al primo avvio)
    if not state.get(f"welcome_v{VERSION}"):
        prices = get_crypto_prices()
        commodities = get_commodity_prices()
        btc = prices.get("BTC", {}).get("price", 0)
        eth = prices.get("ETH", {}).get("price", 0)
        gold = commodities.get("ORO", {}).get("price", 0)

        now_it = now_italy()
        h_utc = (now_it - IT_OFFSET).hour
        active = []
        for k, s in MARKET_SESSIONS.items():
            o, c = s["open_utc"], s["close_utc"]
            if o < c:
                if o <= h_utc < c: active.append(s["emoji"] + " " + s["name"])
            else:
                if h_utc >= o or h_utc < c: active.append(s["emoji"] + " " + s["name"])

        w = (
            f"✅ <b>TradingAlertBot v{VERSION} avviato!</b>\n"
            f"⏰ {format_datetime_it(now_it)} ora italiana\n\n"
            f"<b>📰 FONTI MONITORATE:</b>\n"
            f"📅 ForexFactory (calendario economico)\n"
            f"🪙 CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine\n"
            f"🥇 Investing.com (materie prime + macro)\n"
            f"📰 Bloomberg, CNBC, MarketWatch\n"
            f"📉 FXStreet (forex/commodities)\n\n"
            f"<b>💎 ASSET MONITORATI:</b>\n"
            f"🪙 BTC, ETH, SOL, XRP, ADA, AVAX, LINK, DOT, ONDO\n"
            f"🥇 Oro, Argento, Rame, Petrolio\n"
            f"🏦 BlackRock, Securitize, SPAC/CEPT, Grayscale, Fidelity\n\n"
            f"<b>🔔 ALERT ATTIVI:</b>\n"
            f"📅 Eventi economici ForexFactory\n"
            f"📰 Notizie crypto e macro in tempo reale\n"
            f"📊 Segnali trading con entry, TP, SL (R:R min 1:5)\n"
            f"💼 Allocazione consigliata dopo ogni batch\n"
            f"🧠 Analisi impatto AI per ogni notizia\n"
            f"🧠 Fear &amp; Greed Index\n"
            f"🏛️ Apertura/chiusura sessioni mercato\n\n"
            f"<b>📊 MERCATO ORA:</b>\n"
            f"🟠 BTC: ${btc:,.0f}\n"
            f"🔵 ETH: ${eth:,.0f}\n"
            f"🥇 ORO: ${gold:,.0f}\n"
        )
        if active:
            w += f"\n🟢 Sessioni attive: {', '.join(active)}\n"
        w += f"\n⏱️ Scansione: ogni 3 minuti\n"
        w += f"📊 R:R minimo segnali: 1:{MIN_RR:.0f}\n"
        w += f"💰 Calcolato per operare con 100€\n"

        send_telegram(w)

        # RIEPILOGO ULTIME NOTIZIE ALL'AVVIO
        # IMPORTANTE: NON aggiungiamo gli hash al seen_news!
        # Così nel primo ciclo del loop le notizie verranno inviate normalmente
        log.info("Caricamento riepilogo notizie...")
        all_startup_news = fetch_all_news()
        ff_startup = fetch_forexfactory()
        high_ff_startup = [e for e in ff_startup if "high" in str(e.get("impact", "")).lower()]

        recap = f"📰 <b>RIEPILOGO NOTIZIE DI OGGI</b>\n{'━' * 28}\n\n"

        if high_ff_startup:
            recap += f"📅 <b>EVENTI ECONOMICI (alto impatto):</b>\n"
            for ev in high_ff_startup[:5]:
                title_it = translate(ev.get('title', ''))
                time_utc = ev.get('time_utc', '')
                time_it = ''
                if time_utc and ':' in time_utc:
                    try:
                        parts = time_utc.split(':')
                        h_it = (int(parts[0]) + 2) % 24
                        time_it = f"{h_it:02d}:{parts[1]}"
                    except: time_it = time_utc
                recap += f"  • {escape_html(title_it)}"
                if time_it: recap += f" (ore {time_it})"
                recap += f"\n"
            recap += f"\n"

        scored_startup = []
        for item in all_startup_news:
            sc, lb = assess_impact(item['title'], item.get('description', ''))
            scored_startup.append((sc, lb, item))
        scored_startup.sort(key=lambda x: -x[0])

        if scored_startup:
            recap += f"🔥 <b>NOTIZIE PIÙ IMPORTANTI:</b>\n"
            for sc, lb, item in scored_startup[:8]:
                title_it = translate(item.get('title', ''))
                source = item.get('source', '')
                ntype = item.get('type', '')
                type_em = {'CRYPTO': '🪙', 'COMMODITIES': '🥇', 'MACRO': '🏦', 'FOREX': '💱'}.get(ntype, '📰')
                recap += f"  {type_em} {escape_html(title_it)}\n"
                recap += f"     📰 {escape_html(source)} | {lb}\n\n"

        recap += f"⏰ Aggiornato alle {format_time_it()} ora italiana\n"
        recap += f"🔄 Notizie dettagliate in arrivo tra 3 minuti\n"
        send_telegram(recap)

        # Allocazione iniziale
        sentiments = []
        for sc, lb, item in scored_startup[:10]:
            text = f"{item['title']} {item.get('description', '')}".lower()
            bull_kw = ["approval", "launch", "surge", "rally", "bullish", "record", "ath", "cut", "stimulus", "soar", "gain", "positive", "growth"]
            bear_kw = ["reject", "ban", "crash", "hack", "dump", "bearish", "recession", "collapse", "plunge", "negative", "loss", "decline", "tariff", "hike"]
            b = sum(1 for w in bull_kw if w in text)
            be = sum(1 for w in bear_kw if w in text)
            if b > be: sentiments.append("RIALZISTA")
            elif be > b: sentiments.append("RIBASSISTA")
            else: sentiments.append("NEUTRO")

        alloc_msg = generate_allocation(sentiments, prices, commodities)
        send_telegram(alloc_msg)

        state[f"welcome_v{VERSION}"] = True
        save_json(STATE_FILE, state)
        # NON salviamo seen_news qui - lasciamo vuoto per il primo ciclo

    last_news = 0
    last_prices = 0
    last_sessions = 0
    cycle = 0
    no_news_cycles = 0  # Contatore cicli senza notizie

    while True:
        try:
            cycle += 1
            now_ts = time.time()
            now_utc = datetime.now(timezone.utc)
            log.info(f"--- Ciclo {cycle} ({format_time_it()}) ---")

            # Pulisci giornaliero
            today = now_utc.strftime("%Y-%m-%d")
            if seen.get("last_clean") != today:
                seen["hashes"] = seen["hashes"][-200:]  # Tieni meno hash
                seen["last_clean"] = today
                state["sessions"] = [s for s in state.get("sessions", []) if now_utc.strftime("%Y%m%d") in s]
                state["fng_sent"] = [s for s in state.get("fng_sent", []) if now_italy().strftime("%Y%m%d") in s]
                log.info("Pulizia giornaliera completata")

            # SESSIONI (ogni 60s)
            if now_ts - last_sessions >= SCAN_SESSIONS:
                alerts = check_sessions(state)
                for a in alerts:
                    send_telegram(a)
                    time.sleep(1)
                last_sessions = now_ts

            # PREZZI E SEGNALI TRADING (ogni 5 min)
            crypto_prices = {}
            commodities = {}
            ta_cache = {}
            if now_ts - last_prices >= SCAN_PRICES:
                crypto_prices = get_crypto_prices()
                commodities = get_commodity_prices()
                log.info(f"Prezzi: {len(crypto_prices)} crypto, {len(commodities)} commodities")

                for coin_id in ["bitcoin", "ethereum", "solana"]:
                    ta = get_technical_analysis(coin_id)
                    if ta:
                        ta_cache[coin_id] = ta
                    time.sleep(1)

                fng_msg = check_fng_alert(state)
                if fng_msg:
                    send_telegram(fng_msg)

                signals = check_price_signals(crypto_prices, commodities, price_history, ta_cache, state)
                for s in signals[:3]:
                    send_telegram(s)
                    time.sleep(1)

                save_json(PRICE_HISTORY_FILE, price_history)
                last_prices = now_ts

            # NOTIZIE (ogni 3 min)
            if now_ts - last_news >= SCAN_NEWS:
                if not crypto_prices:
                    crypto_prices = get_crypto_prices()
                    commodities = get_commodity_prices()

                # ForexFactory
                ff = fetch_forexfactory()
                high_ff = [e for e in ff if "high" in str(e.get("impact", "")).lower()]
                ff_sent = 0
                for ev in high_ff[:3]:
                    h = news_hash(ev["title"], "FF")
                    if h not in seen["hashes"]:
                        msg = format_calendar(ev, {**crypto_prices, **commodities})
                        send_telegram(msg)
                        seen["hashes"].append(h)
                        ff_sent += 1
                        time.sleep(1)
                log.info(f"ForexFactory: {len(high_ff)} alto impatto, {ff_sent} nuovi inviati")

                # Notizie da RSS
                all_news = fetch_all_news()
                scored = []
                new_count = 0
                for item in all_news:
                    h = news_hash(item["title"], item["source"])
                    if h in seen["hashes"]:
                        continue
                    new_count += 1
                    sc, lb = assess_impact(item["title"], item.get("description", ""))
                    scored.append((sc, lb, item, h))
                scored.sort(key=lambda x: -x[0])

                log.info(f"Notizie: {len(all_news)} totali, {new_count} nuove, {len(scored)} dopo filtro")

                sent = 0
                batch_sentiments = []
                for sc, lb, item, h in scored:
                    # Invia TUTTE le notizie nuove (score >= 1)
                    if sent >= 5: break  # Max 5 per ciclo
                    if is_quiet_hours() and sc < 4: continue

                    assets = find_affected_assets(item["title"], item.get("description", ""))
                    strat, sentiment = generate_news_strategy(item["title"], item.get("description", ""),
                                                    sc, assets, crypto_prices, commodities, ta_cache)
                    # Analisi impatto AI per notizie con score >= 2
                    impact_ai = ""
                    if sc >= 2:
                        impact_ai = analyze_news_impact(item["title"], item.get("description", ""),
                                                        assets, crypto_prices, commodities)
                    msg = format_news(item, sc, lb, assets, strat, impact_ai)
                    send_telegram(msg)
                    seen["hashes"].append(h)
                    batch_sentiments.append(sentiment)
                    sent += 1
                    time.sleep(2)

                log.info(f"Notizie inviate: {sent}")

                # ALLOCAZIONE CONSIGLIATA dopo batch di notizie
                if sent > 0:
                    alloc_msg = generate_allocation(batch_sentiments, crypto_prices, commodities)
                    send_telegram(alloc_msg)
                    no_news_cycles = 0
                else:
                    no_news_cycles += 1
                    log.info(f"Nessuna notizia nuova (ciclo {no_news_cycles})")

                    # Se dopo 3 cicli (9 min) nessuna notizia, invia status
                    if no_news_cycles >= 3 and no_news_cycles % 10 == 3:
                        btc_p = crypto_prices.get("BTC", {}).get("price", 0)
                        eth_p = crypto_prices.get("ETH", {}).get("price", 0)
                        status = f"📊 <b>AGGIORNAMENTO</b>\n{'━' * 28}\n"
                        status += f"Nessuna notizia rilevante negli ultimi minuti.\n"
                        status += f"Il mercato è tranquillo.\n\n"
                        if btc_p:
                            status += f"🟠 BTC: ${btc_p:,.0f}\n"
                        if eth_p:
                            status += f"🔵 ETH: ${eth_p:,.0f}\n"
                        status += f"\n⏰ {format_time_it()} | Prossimo check tra 3 min\n"
                        if not is_quiet_hours():
                            send_telegram(status)

                save_json(SEEN_FILE, seen)
                last_news = now_ts

            save_json(STATE_FILE, state)
            log.info(f"Ciclo {cycle} OK. Prossimo tra 30s")

        except Exception as e:
            log.error(f"Errore ciclo {cycle}: {e}", exc_info=True)

        time.sleep(30)

if __name__ == "__main__":
    main()
