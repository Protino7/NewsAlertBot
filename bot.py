#!/usr/bin/env python3
"""
TradingAlertBot v3.0 - Decision Engine Multi-Layer
Bot Telegram: analisi eventi strutturata, multi-factor scoring,
smart money detection, conflict resolution, confidence system.
Tutto in italiano, orari italiani.

Fonti: ForexFactory, CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine,
       Investing.com, Bloomberg, CNBC, FXStreet, MarketWatch, CoinGecko
"""

import os, sys, time, json, re, hashlib, logging, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
VERSION = "3.0.0"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8315107370:AAHA12xJb6VIklrhZj93OYoa2FyI8937H_U")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "291291784")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Timing
SCAN_NEWS = 180       # 3 min
SCAN_PRICES = 300     # 5 min
SCAN_SESSIONS = 60    # 1 min
LOOP_SLEEP = 30       # 30s

# Files
STATE_FILE = "state.json"
SEEN_FILE = "seen_news.json"
PRICE_HISTORY_FILE = "price_history.json"
FORWARD_TEST_FILE = "forward_test.json"
WEEKLY_REPORT_FILE = "weekly_report.json"

# Timezone
IT_OFFSET = timedelta(hours=2)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------------------------
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip3 install requests beautifulsoup4 lxml")
    import requests
    from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------
def load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def now_italy():
    return datetime.now(timezone.utc) + IT_OFFSET

def format_time_it():
    return now_italy().strftime("%H:%M")

def utc_to_italy(h_utc):
    return f"{(h_utc + 2) % 24:02d}:00"

def escape_html(text):
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def news_hash(title, source):
    return hashlib.md5(f"{title[:50]}{source}".encode()).hexdigest()[:12]

def send_telegram(text, retries=3):
    for i in range(retries):
        try:
            r = requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True
            }, timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait)
                continue
            log.warning(f"Telegram {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.warning(f"Telegram error: {e}")
        time.sleep(2)
    return False

def is_quiet_hours():
    h = now_italy().hour
    return h >= 23 or h < 6

# ---------------------------------------------------------------------------
# TRANSLATION (basic EN->IT)
# ---------------------------------------------------------------------------
TRANSLATE_MAP = {
    "bitcoin": "Bitcoin", "ethereum": "Ethereum", "crypto": "crypto",
    "federal reserve": "Federal Reserve", "interest rate": "tasso di interesse",
    "inflation": "inflazione", "recession": "recessione", "rally": "rally",
    "crash": "crollo", "surge": "impennata", "plunge": "crollo",
    "approval": "approvazione", "rejection": "rigetto", "ban": "divieto",
    "regulation": "regolamentazione", "partnership": "partnership",
    "acquisition": "acquisizione", "merger": "fusione", "hack": "hack",
    "whale": "whale", "institutional": "istituzionale",
    "bullish": "rialzista", "bearish": "ribassista",
    "all-time high": "massimo storico", "dump": "dump",
    "listing": "listing", "delist": "delisting",
    "halving": "halving", "staking": "staking", "mining": "mining",
    "etf": "ETF", "sec": "SEC", "fed": "Fed",
}

def translate(text):
    if not text:
        return ""
    result = text
    for en, it in TRANSLATE_MAP.items():
        result = re.sub(re.escape(en), it, result, flags=re.IGNORECASE)
    return result[:500]

# ---------------------------------------------------------------------------
# DATA FETCHING - PRICES
# ---------------------------------------------------------------------------
def get_crypto_prices():
    prices = {}
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": "bitcoin,ethereum,solana,ripple,cardano,avalanche-2,chainlink,polkadot,binancecoin",
                                 "vs_currencies": "usd", "include_24hr_change": "true"},
                         timeout=10)
        if r.status_code == 200:
            data = r.json()
            mapping = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
                       "ripple": "XRP", "cardano": "ADA", "avalanche-2": "AVAX",
                       "chainlink": "LINK", "polkadot": "DOT", "binancecoin": "BNB"}
            for cg_id, symbol in mapping.items():
                if cg_id in data:
                    prices[symbol] = {
                        "price": data[cg_id].get("usd", 0),
                        "change_24h": data[cg_id].get("usd_24h_change", 0)
                    }
        else:
            log.warning(f"GET CoinGecko prices: HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"CoinGecko error: {e}")
    return prices

def get_commodity_prices():
    commodities = {}
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": "tether-gold", "vs_currencies": "usd",
                                 "include_24hr_change": "true"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "tether-gold" in data:
                commodities["ORO"] = {
                    "price": data["tether-gold"].get("usd", 0),
                    "change_24h": data["tether-gold"].get("usd_24h_change", 0)
                }
    except:
        pass
    return commodities

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=2", timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if len(data) >= 2:
                return {
                    "value": int(data[0]["value"]),
                    "label": data[0]["value_classification"],
                    "previous": int(data[1]["value"])
                }
    except:
        pass
    return None

# ---------------------------------------------------------------------------
# DATA FETCHING - NEWS
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", "CRYPTO"),
    ("https://cointelegraph.com/rss", "CoinTelegraph", "CRYPTO"),
    ("https://decrypt.co/feed", "Decrypt", "CRYPTO"),
    ("https://bitcoinmagazine.com/feed", "Bitcoin Magazine", "CRYPTO"),
    ("https://www.investing.com/rss/news_301.rss", "Investing.com", "MACRO"),
    ("https://www.investing.com/rss/news_1.rss", "Investing.com", "MACRO"),
    ("https://www.investing.com/rss/news_14.rss", "Investing.com", "COMMODITIES"),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch", "MACRO"),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC", "MACRO"),
    ("https://www.fxstreet.com/rss", "FXStreet", "FOREX"),
    ("https://feeds.bloomberg.com/markets/news.rss", "Bloomberg", "MACRO"),
]

def fetch_all_news():
    all_news = []
    for url, source, ntype in RSS_FEEDS:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                log.warning(f"GET {url[:60]}: HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.content, "xml")
            items = soup.find_all("item")[:8]
            for item in items:
                title = item.find("title")
                desc = item.find("description")
                link = item.find("link")
                pub = item.find("pubDate")
                all_news.append({
                    "source": source,
                    "title": title.get_text(strip=True) if title else "",
                    "description": desc.get_text(strip=True)[:400] if desc else "",
                    "link": link.get_text(strip=True) if link else "",
                    "published": pub.get_text(strip=True) if pub else "",
                    "type": ntype
                })
            log.info(f"RSS {source}: {len(items)} notizie")
        except Exception as e:
            log.warning(f"GET {url[:60]}: {e}")
            log.info(f"RSS {source}: 0 notizie")
    log.info(f"TOTALE notizie raccolte: {len(all_news)}")
    return all_news

def fetch_forexfactory():
    events = []
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        if r.status_code == 200:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for ev in r.json():
                if today in ev.get("date", ""):
                    events.append(ev)
        else:
            log.warning(f"GET ForexFactory: HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"ForexFactory error: {e}")
    log.info(f"ForexFactory: {len(events)} eventi oggi")
    return events

# ---------------------------------------------------------------------------
# 🧠 1. EVENT ENGINE - Comprensione strutturata
# ---------------------------------------------------------------------------

# Actor detection keywords
ACTOR_KEYWORDS = {
    "CENTRAL_BANK": {
        "weight": 5,
        "keywords": ["fed", "federal reserve", "ecb", "bce", "boj", "bank of japan",
                     "bank of england", "boe", "central bank", "banca centrale",
                     "powell", "lagarde", "rate decision", "monetary policy",
                     "quantitative", "tightening", "easing", "fomc"]
    },
    "INSTITUTIONAL": {
        "weight": 4,
        "keywords": ["blackrock", "fidelity", "vanguard", "grayscale", "microstrategy",
                     "goldman sachs", "jp morgan", "morgan stanley", "citadel",
                     "institutional", "istituzional", "etf", "fund", "asset manager",
                     "securitize", "custody", "pension", "endowment", "sovereign"]
    },
    "WHALE": {
        "weight": 3,
        "keywords": ["whale", "large holder", "dormant wallet", "billion",
                     "massive transfer", "large transaction", "smart money",
                     "accumulation address", "top holder"]
    },
    "RETAIL": {
        "weight": 1,
        "keywords": ["retail", "small investor", "reddit", "social media",
                     "fomo", "meme", "trending", "viral", "community"]
    }
}

# Event type classification
EVENT_TYPES = {
    "MACRO": ["fed", "ecb", "inflation", "cpi", "gdp", "unemployment", "nfp",
              "rate", "tariff", "trade war", "sanctions", "recession", "fiscal",
              "treasury", "bond", "yield", "dollar", "euro", "forex", "oil",
              "gold", "commodity", "economic data", "pmi", "manufacturing"],
    "REGOLATORIO": ["sec", "regulation", "ban", "law", "legal", "compliance",
                    "license", "approval", "reject", "framework", "legislation",
                    "court", "lawsuit", "fine", "penalty", "enforcement"],
    "ON_CHAIN": ["whale", "transfer", "wallet", "on-chain", "blockchain",
                 "transaction", "mining", "hash rate", "difficulty",
                 "staking", "defi", "liquidity", "tvl", "protocol",
                 "smart contract", "bridge", "hack", "exploit"],
    "TECNICO": ["breakout", "support", "resistance", "rsi", "macd",
                "moving average", "volume", "pattern", "fibonacci",
                "trend", "reversal", "consolidation", "divergence"]
}

def classify_event_type(text):
    """Classifica il tipo di evento."""
    text_lower = text.lower()
    scores = {}
    for etype, keywords in EVENT_TYPES.items():
        scores[etype] = sum(1 for kw in keywords if kw in text_lower)
    if max(scores.values()) == 0:
        return "MACRO"  # default
    return max(scores, key=scores.get)

def detect_actors(text):
    """Rileva gli attori coinvolti e il loro peso."""
    text_lower = text.lower()
    actors = []
    for actor_type, data in ACTOR_KEYWORDS.items():
        matches = sum(1 for kw in data["keywords"] if kw in text_lower)
        if matches > 0:
            actors.append({
                "type": actor_type,
                "weight": data["weight"],
                "matches": matches
            })
    actors.sort(key=lambda x: -x["weight"])
    return actors

def extract_event(title, desc):
    """Estrae la struttura completa dell'evento."""
    full_text = f"{title} {desc}"
    return {
        "title": title,
        "type": classify_event_type(full_text),
        "actors": detect_actors(full_text),
        "max_actor_weight": max([a["weight"] for a in detect_actors(full_text)]) if detect_actors(full_text) else 1
    }

# ---------------------------------------------------------------------------
# 🔍 2. CAUSA → EFFETTO Engine
# ---------------------------------------------------------------------------

CAUSE_PATTERNS = {
    # Pattern: (cause_keywords, effect_description)
    "rate_hike": {
        "cause": ["rate hike", "rate increase", "hawkish", "tightening"],
        "effect": "Liquidità ridotta → pressione ribassista su risk assets",
        "direction": -2
    },
    "rate_cut": {
        "cause": ["rate cut", "rate decrease", "dovish", "easing", "pivot"],
        "effect": "Liquidità aumentata → supporto per risk assets e crypto",
        "direction": 2
    },
    "etf_approval": {
        "cause": ["etf approved", "etf approval", "etf launch"],
        "effect": "Flussi istituzionali massicci → forte pressione rialzista",
        "direction": 3
    },
    "etf_rejection": {
        "cause": ["etf rejected", "etf denial", "etf postpone"],
        "effect": "Delusione mercato → sell-off breve termine",
        "direction": -2
    },
    "institutional_buy": {
        "cause": ["blackrock buy", "fidelity buy", "institutional purchase",
                  "microstrategy buy", "treasury purchase", "etf inflow"],
        "effect": "Smart money in accumulo → segnale bullish medio termine",
        "direction": 2
    },
    "hack_exploit": {
        "cause": ["hack", "exploit", "breach", "stolen", "drained"],
        "effect": "Perdita fiducia → panic sell breve termine, recupero se protocollo solido",
        "direction": -2
    },
    "ban_restriction": {
        "cause": ["ban", "prohibit", "restrict", "outlaw", "illegal"],
        "effect": "Incertezza normativa → pressione ribassista, ma storicamente temporanea",
        "direction": -2
    },
    "adoption": {
        "cause": ["adopt", "accept", "integrate", "partnership", "launch"],
        "effect": "Espansione ecosistema → supporto fondamentale rialzista",
        "direction": 1
    },
    "whale_accumulation": {
        "cause": ["whale buy", "large purchase", "accumulation", "dormant wallet active"],
        "effect": "Smart money posizionamento → possibile movimento rialzista imminente",
        "direction": 2
    },
    "panic_sell": {
        "cause": ["panic", "capitulation", "mass sell", "liquidation", "crash"],
        "effect": "Eccesso ribassista → possibile bottom locale se smart money non vende",
        "direction": -1  # Contrarian: panic = possibile opportunità
    },
    "inflation_high": {
        "cause": ["inflation rise", "cpi above", "inflation higher", "price pressure"],
        "effect": "Fed più hawkish → pressione su tutti i mercati, ORO come hedge",
        "direction": -2
    },
    "inflation_low": {
        "cause": ["inflation fall", "cpi below", "inflation lower", "disinflation"],
        "effect": "Fed più dovish → supporto per risk assets e crypto",
        "direction": 2
    },
    "geopolitical": {
        "cause": ["war", "conflict", "tension", "sanction", "missile", "invasion"],
        "effect": "Flight to safety → ORO e USD su, risk assets giù nel breve",
        "direction": -2
    },
    "tariff": {
        "cause": ["tariff", "trade war", "import duty", "trade restriction"],
        "effect": "Incertezza commerciale → volatilità, BTC come hedge geopolitico",
        "direction": -1
    }
}

def analyze_cause_effect(text):
    """Analizza causa-effetto della notizia."""
    text_lower = text.lower()
    matched = []
    for pattern_name, data in CAUSE_PATTERNS.items():
        for cause_kw in data["cause"]:
            if cause_kw in text_lower:
                matched.append({
                    "pattern": pattern_name,
                    "effect": data["effect"],
                    "direction": data["direction"]
                })
                break
    return matched

# ---------------------------------------------------------------------------
# ⚖️ 3. MULTI-FACTOR SCORING ENGINE
# ---------------------------------------------------------------------------

def calculate_multi_factor_score(event, cause_effects, crypto_prices, fng_data):
    """Calcola punteggi separati per short/mid/long term."""
    
    # Base direction from cause-effect
    base_direction = 0
    if cause_effects:
        base_direction = sum(ce["direction"] for ce in cause_effects) / len(cause_effects)
    
    # Actor weight factor
    actor_factor = event["max_actor_weight"] / 5.0  # Normalizzato 0-1
    
    # Price momentum
    price_momentum = 0
    btc_change = crypto_prices.get("BTC", {}).get("change_24h", 0)
    if btc_change > 3:
        price_momentum = 2
    elif btc_change > 1:
        price_momentum = 1
    elif btc_change < -3:
        price_momentum = -2
    elif btc_change < -1:
        price_momentum = -1
    
    # Market sentiment (Fear & Greed)
    sentiment_factor = 0
    if fng_data:
        val = fng_data["value"]
        if val <= 20:
            sentiment_factor = -2  # Extreme fear (contrarian = opportunity)
        elif val <= 35:
            sentiment_factor = -1
        elif val >= 80:
            sentiment_factor = 2  # Extreme greed (contrarian = risk)
        elif val >= 65:
            sentiment_factor = 1
    
    # Calculate scores per timeframe
    short_term = round(base_direction * 1.5 + price_momentum * 0.5, 1)
    mid_term = round(base_direction * 1.0 + actor_factor * 2 + sentiment_factor * -0.5, 1)
    long_term = round(base_direction * 0.5 + actor_factor * 3, 1)
    
    # Clamp to -5/+5
    short_term = max(-5, min(5, short_term))
    mid_term = max(-5, min(5, mid_term))
    long_term = max(-5, min(5, long_term))
    
    return {
        "short_term": short_term,
        "mid_term": mid_term,
        "long_term": long_term,
        "factors": {
            "direction": round(base_direction, 1),
            "actor_weight": round(actor_factor * 5, 1),
            "price_momentum": price_momentum,
            "sentiment": sentiment_factor
        }
    }

# ---------------------------------------------------------------------------
# 🧨 4. SMART MONEY DETECTOR
# ---------------------------------------------------------------------------

def detect_smart_money(text, actors, price_momentum):
    """Rileva smart money accumulation o dumb money exit."""
    text_lower = text.lower()
    
    has_institutional = any(a["type"] in ("CENTRAL_BANK", "INSTITUTIONAL") for a in actors)
    has_whale = any(a["type"] == "WHALE" for a in actors)
    has_retail = any(a["type"] == "RETAIL" for a in actors)
    
    # Buy keywords
    buy_kw = ["buy", "purchase", "accumulate", "acquire", "inflow", "add"]
    sell_kw = ["sell", "dump", "outflow", "liquidat", "panic", "exit"]
    
    is_buying = any(kw in text_lower for kw in buy_kw)
    is_selling = any(kw in text_lower for kw in sell_kw)
    
    result = {"detected": False, "type": None, "label": None}
    
    # Smart money accumulation: istituzionali/whale comprano, specialmente in ribasso
    if (has_institutional or has_whale) and is_buying:
        result = {
            "detected": True,
            "type": "ACCUMULATION",
            "label": "🧨 SMART MONEY ACCUMULATION"
        }
        if price_momentum < 0:
            result["label"] = "🧨 SMART MONEY ACCUMULATION (in ribasso = segnale forte)"
    
    # Dumb money exit: retail vende in panico
    elif has_retail and is_selling:
        result = {
            "detected": True,
            "type": "DUMB_MONEY_EXIT",
            "label": "💸 DUMB MONEY EXIT (retail in panico)"
        }
    
    # Distribuzione: istituzionali vendono mentre retail compra
    elif (has_institutional or has_whale) and is_selling and has_retail and is_buying:
        result = {
            "detected": True,
            "type": "DISTRIBUTION",
            "label": "⚠️ DISTRIBUZIONE (smart money vende a retail)"
        }
    
    return result

# ---------------------------------------------------------------------------
# 🧠 7. MARKET PHASE CLASSIFIER
# ---------------------------------------------------------------------------

def classify_market_phase(crypto_prices, fng_data, smart_money):
    """Classifica la fase di mercato attuale."""
    btc_change = crypto_prices.get("BTC", {}).get("change_24h", 0)
    fng_val = fng_data["value"] if fng_data else 50
    
    # Panic: FNG < 20 e prezzo in forte calo
    if fng_val <= 20 and btc_change < -3:
        return "PANIC", "😱 Mercato in panico – possibile capitolazione"
    
    # Accumulation: FNG basso ma smart money compra
    if fng_val <= 35 and smart_money.get("type") == "ACCUMULATION":
        return "ACCUMULATION", "🟢 Fase di accumulo – smart money posizionato"
    
    # Distribution: FNG alto e smart money vende
    if fng_val >= 70 and smart_money.get("type") == "DISTRIBUTION":
        return "DISTRIBUTION", "🔴 Fase di distribuzione – cautela massima"
    
    # Trend rialzista: prezzo su, FNG moderato-alto
    if btc_change > 2 and fng_val >= 50:
        return "TREND_UP", "📈 Trend rialzista attivo"
    
    # Trend ribassista: prezzo giù, FNG basso
    if btc_change < -2 and fng_val <= 40:
        return "TREND_DOWN", "📉 Trend ribassista attivo"
    
    # Chop/laterale
    if abs(btc_change) < 1.5:
        return "CHOP", "↔️ Mercato laterale – nessuna direzione chiara"
    
    return "NEUTRAL", "⚪ Fase neutra"

# ---------------------------------------------------------------------------
# 🔄 5. CONFLICT RESOLUTION ENGINE
# ---------------------------------------------------------------------------

def resolve_conflicts(scores, smart_money, market_phase):
    """Risolve conflitti tra segnali."""
    short = scores["short_term"]
    mid = scores["mid_term"]
    long_t = scores["long_term"]
    
    # Controlla se i segnali sono allineati
    all_positive = short > 0 and mid > 0 and long_t > 0
    all_negative = short < 0 and mid < 0 and long_t < 0
    
    # Conflitto: direzioni opposte tra timeframe
    if (short > 1 and mid < -1) or (short < -1 and mid > 1):
        return {
            "signal": "MISTO",
            "reason": "Segnali contrastanti tra breve e medio termine",
            "action": "ATTENDERE",
            "conflict": True
        }
    
    # Smart money contrarian
    if smart_money.get("type") == "ACCUMULATION" and short < 0:
        return {
            "signal": "ACCUMULO",
            "reason": "Smart money accumula in ribasso – possibile inversione",
            "action": "ACCUMULARE_GRADUALMENTE",
            "conflict": False
        }
    
    if smart_money.get("type") == "DISTRIBUTION" and short > 0:
        return {
            "signal": "DISTRIBUZIONE",
            "reason": "Smart money distribuisce durante il rialzo – possibile top",
            "action": "RIDURRE_RISCHIO",
            "conflict": False
        }
    
    # Tutto allineato bullish
    if all_positive:
        return {
            "signal": "BULLISH",
            "reason": "Tutti i fattori allineati al rialzo",
            "action": "SEGUIRE_TREND",
            "conflict": False
        }
    
    # Tutto allineato bearish
    if all_negative:
        return {
            "signal": "BEARISH",
            "reason": "Tutti i fattori allineati al ribasso",
            "action": "RIDURRE_RISCHIO",
            "conflict": False
        }
    
    # Incertezza
    return {
        "signal": "INCERTO",
        "reason": "Nessun edge chiaro – fattori non allineati",
        "action": "NESSUNA_AZIONE",
        "conflict": True
    }

# ---------------------------------------------------------------------------
# 🧠 9. CONFIDENCE SYSTEM
# ---------------------------------------------------------------------------

def calculate_confidence(scores, smart_money, resolution, actors):
    """Calcola confidence 1-5."""
    confidence = 3  # Base
    
    # Allineamento timeframe
    signs = [1 if s > 0 else (-1 if s < 0 else 0) 
             for s in [scores["short_term"], scores["mid_term"], scores["long_term"]]]
    if all(s == signs[0] and s != 0 for s in signs):
        confidence += 1  # Tutti allineati
    elif signs[0] != 0 and signs[0] != signs[1]:
        confidence -= 1  # Conflitto
    
    # Smart money presente
    if smart_money.get("detected"):
        confidence += 1
    
    # Attori pesanti coinvolti
    if actors and actors[0]["weight"] >= 4:
        confidence += 0.5
    
    # Conflitto rilevato
    if resolution.get("conflict"):
        confidence -= 1
    
    # Intensità del segnale
    max_score = max(abs(scores["short_term"]), abs(scores["mid_term"]), abs(scores["long_term"]))
    if max_score < 1:
        confidence -= 1
    
    return max(1, min(5, round(confidence)))

# ---------------------------------------------------------------------------
# 📉 10. RISK FILTER
# ---------------------------------------------------------------------------

def apply_risk_filter(confidence, resolution):
    """Applica filtro anti-overtrading."""
    if confidence < 3 and resolution["action"] not in ("NESSUNA_AZIONE", "ATTENDERE"):
        return {
            "filtered": True,
            "original_action": resolution["action"],
            "new_action": "ATTENDERE",
            "reason": "Confidence troppo bassa per azione forte"
        }
    return {"filtered": False}

# ---------------------------------------------------------------------------
# 🛑 6. NO-HALLUCINATION FILTER
# ---------------------------------------------------------------------------

def validate_analysis(title, desc, cause_effects, actors):
    """Verifica che l'analisi sia coerente con la news."""
    text_lower = f"{title} {desc}".lower()
    warnings = []
    
    # Verifica che gli attori rilevati siano effettivamente menzionati
    for actor in actors:
        actor_mentioned = any(kw in text_lower for kw in ACTOR_KEYWORDS[actor["type"]]["keywords"])
        if not actor_mentioned:
            warnings.append(f"Attore {actor['type']} non confermato nel testo")
    
    return warnings

# ---------------------------------------------------------------------------
# ASSET DETECTION
# ---------------------------------------------------------------------------

def find_affected_assets(text):
    """Trova gli asset menzionati nella notizia."""
    text_lower = text.lower()
    assets = []
    mapping = {
        "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
        "solana": "SOL", "sol": "SOL", "ripple": "XRP", "xrp": "XRP",
        "cardano": "ADA", "avalanche": "AVAX", "chainlink": "LINK",
        "polkadot": "DOT", "bnb": "BNB",
        "gold": "ORO", "oro": "ORO", "oil": "PETROLIO", "crude": "PETROLIO",
        "dollar": "USD", "euro": "EUR", "s&p": "S&P500", "nasdaq": "NASDAQ",
    }
    for kw, asset in mapping.items():
        if kw in text_lower and asset not in assets:
            assets.append(asset)
    return assets

# ---------------------------------------------------------------------------
# 📊 FORMATO OUTPUT STRUTTURATO
# ---------------------------------------------------------------------------

def format_decision_output(item, event, cause_effects, scores, smart_money, 
                           market_phase, resolution, confidence, risk_filter,
                           assets, crypto_prices):
    """Formatta l'output strutturato del Decision Engine."""
    
    title_it = translate(item.get("title", ""))
    source = item.get("source", "")
    link = item.get("link", "")
    
    # Header basato su confidence
    if confidence >= 4:
        header = f"🎯🎯🎯 <b>SEGNALE FORTE (Confidence {confidence}/5)</b>\n{'━' * 30}\n"
    elif confidence >= 3:
        header = f"📊📊 <b>ANALISI EVENTO (Confidence {confidence}/5)</b>\n{'━' * 30}\n"
    else:
        header = f"📰 <b>EVENTO (Confidence {confidence}/5)</b>\n{'━' * 30}\n"
    
    msg = header
    
    # 📊 ANALISI EVENTO
    msg += f"\n📊 <b>ANALISI EVENTO</b>\n"
    msg += f"📌 <b>{escape_html(title_it)}</b>\n"
    msg += f"📁 Tipo: <b>{event['type']}</b>\n"
    
    # Attori
    if event["actors"]:
        actors_str = ", ".join([f"{a['type']}(peso {a['weight']})" for a in event["actors"][:3]])
        msg += f"👥 Attori: {actors_str}\n"
    else:
        msg += f"👥 Attori: Non identificati\n"
    
    # Causa-Effetto
    if cause_effects:
        msg += f"\n🔍 <b>CAUSA → EFFETTO</b>\n"
        for ce in cause_effects[:2]:
            msg += f"  → {escape_html(ce['effect'])}\n"
    
    # 🧠 INTERPRETAZIONE
    msg += f"\n🧠 <b>INTERPRETAZIONE</b>\n"
    
    st_emoji = "🟢" if scores["short_term"] > 0 else ("🔴" if scores["short_term"] < 0 else "⚪")
    mt_emoji = "🟢" if scores["mid_term"] > 0 else ("🔴" if scores["mid_term"] < 0 else "⚪")
    lt_emoji = "🟢" if scores["long_term"] > 0 else ("🔴" if scores["long_term"] < 0 else "⚪")
    
    msg += f"  {st_emoji} Breve (0-7gg): {scores['short_term']:+.1f}\n"
    msg += f"  {mt_emoji} Medio (1-4 sett): {scores['mid_term']:+.1f}\n"
    msg += f"  {lt_emoji} Lungo (1-6 mesi): {scores['long_term']:+.1f}\n"
    
    # 🧨 SMART MONEY
    msg += f"\n🧨 <b>SMART MONEY</b>\n"
    if smart_money["detected"]:
        msg += f"  {smart_money['label']}\n"
    else:
        msg += f"  ❌ Non rilevato\n"
    
    # 🏛️ FASE MERCATO
    phase_name, phase_desc = market_phase
    msg += f"\n🏛️ <b>FASE:</b> {phase_desc}\n"
    
    # ⚖️ SEGNALE
    signal_emoji = {
        "BULLISH": "🟢", "BEARISH": "🔴", "MISTO": "🟡",
        "ACCUMULO": "🟢", "DISTRIBUZIONE": "🔴", "INCERTO": "⚪"
    }
    msg += f"\n⚖️ <b>SEGNALE:</b> {signal_emoji.get(resolution['signal'], '⚪')} {resolution['signal']}\n"
    msg += f"  💬 {escape_html(resolution['reason'])}\n"
    
    # 🎯 AZIONE
    action_map = {
        "ATTENDERE": "⏳ Attendere conferme",
        "ACCUMULARE_GRADUALMENTE": "🟢 Accumulare gradualmente (DCA)",
        "RIDURRE_RISCHIO": "🔴 Ridurre rischio / prendere profitto",
        "SEGUIRE_TREND": "📈 Seguire il trend attivo",
        "NESSUNA_AZIONE": "⏸️ Nessuna azione – edge insufficiente"
    }
    
    final_action = resolution["action"]
    if risk_filter.get("filtered"):
        final_action = risk_filter["new_action"]
        msg += f"\n🛡️ <b>RISK FILTER:</b> Azione ridotta (confidence bassa)\n"
    
    msg += f"\n🎯 <b>AZIONE:</b> {action_map.get(final_action, final_action)}\n"
    
    # Confidence bar
    conf_bar = "🟢" * confidence + "⚪" * (5 - confidence)
    msg += f"💡 <b>Confidence:</b> {conf_bar} ({confidence}/5)\n"
    
    # Asset e prezzi
    if assets and crypto_prices:
        msg += f"\n💎 <b>Asset coinvolti:</b>\n"
        for asset in assets[:4]:
            p_data = crypto_prices.get(asset)
            if p_data:
                ch = p_data.get("change_24h", 0)
                ch_emoji = "📈" if ch > 0 else "📉"
                msg += f"  {ch_emoji} {asset}: ${p_data['price']:,.0f} ({ch:+.1f}%)\n"
    
    # Footer
    if link:
        msg += f"\n🔗 <a href=\"{escape_html(link)}\">Fonte</a>"
    msg += f"\n⏰ {format_time_it()} | 📰 {escape_html(source)}\n"
    
    return msg

# ---------------------------------------------------------------------------
# 🔄 11. FORWARD TEST LOGGING
# ---------------------------------------------------------------------------

def log_forward_test(item, resolution, confidence, scores, crypto_prices, assets):
    """Salva il segnale per verifica futura."""
    ft = load_json(FORWARD_TEST_FILE, {"signals": []})
    
    # Prezzo al momento del segnale
    prices_at_signal = {}
    for asset in assets[:5]:
        p_data = crypto_prices.get(asset)
        if p_data:
            prices_at_signal[asset] = p_data["price"]
    
    signal_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": item.get("title", "")[:100],
        "signal": resolution["signal"],
        "action": resolution["action"],
        "confidence": confidence,
        "scores": scores,
        "prices_at_signal": prices_at_signal,
        "assets": assets[:5],
        # Questi verranno riempiti dal check successivo
        "price_after_1d": None,
        "price_after_7d": None,
        "verified": False
    }
    
    ft["signals"].append(signal_entry)
    # Mantieni solo ultimi 500 segnali
    ft["signals"] = ft["signals"][-500:]
    save_json(FORWARD_TEST_FILE, ft)

# ---------------------------------------------------------------------------
# 📊 12. REPORT SETTIMANALE
# ---------------------------------------------------------------------------

def generate_weekly_report(state):
    """Genera report settimanale ogni domenica."""
    now = now_italy()
    if now.weekday() != 6:  # Solo domenica
        return None
    
    today_key = now.strftime("%Y%m%d")
    if today_key in state.get("weekly_reports_sent", []):
        return None
    
    ft = load_json(FORWARD_TEST_FILE, {"signals": []})
    signals = ft.get("signals", [])
    
    if len(signals) < 5:
        return None
    
    # Ultimi 7 giorni
    week_ago = (now - timedelta(days=7)).isoformat()
    recent = [s for s in signals if s.get("timestamp", "") >= week_ago]
    
    if not recent:
        return None
    
    # Statistiche
    total = len(recent)
    bullish = sum(1 for s in recent if s["signal"] == "BULLISH")
    bearish = sum(1 for s in recent if s["signal"] == "BEARISH")
    mixed = sum(1 for s in recent if s["signal"] in ("MISTO", "INCERTO"))
    accumulo = sum(1 for s in recent if s["signal"] == "ACCUMULO")
    
    avg_confidence = sum(s["confidence"] for s in recent) / total
    high_conf = sum(1 for s in recent if s["confidence"] >= 4)
    low_conf = sum(1 for s in recent if s["confidence"] <= 2)
    
    # Azioni consigliate
    actions = {}
    for s in recent:
        a = s.get("action", "UNKNOWN")
        actions[a] = actions.get(a, 0) + 1
    
    msg = f"📊 <b>REPORT SETTIMANALE v3.0</b>\n{'━' * 30}\n"
    msg += f"📅 Settimana: {(now - timedelta(days=7)).strftime('%d/%m')} - {now.strftime('%d/%m/%Y')}\n\n"
    
    msg += f"📈 <b>STATISTICHE SEGNALI</b>\n"
    msg += f"  Totale analisi: {total}\n"
    msg += f"  🟢 Bullish: {bullish} ({bullish*100//total}%)\n"
    msg += f"  🔴 Bearish: {bearish} ({bearish*100//total}%)\n"
    msg += f"  🟡 Misti/Incerti: {mixed} ({mixed*100//total}%)\n"
    msg += f"  🟢 Accumulo: {accumulo}\n\n"
    
    msg += f"💡 <b>CONFIDENCE</b>\n"
    msg += f"  Media: {avg_confidence:.1f}/5\n"
    msg += f"  Alta (≥4): {high_conf}\n"
    msg += f"  Bassa (≤2): {low_conf}\n\n"
    
    msg += f"🎯 <b>AZIONI CONSIGLIATE</b>\n"
    for action, count in sorted(actions.items(), key=lambda x: -x[1]):
        msg += f"  {action}: {count}x\n"
    
    msg += f"\n⚠️ <b>NOTE</b>\n"
    if avg_confidence < 3:
        msg += f"  ⚠️ Confidence media bassa – mercato incerto\n"
    if mixed > total * 0.5:
        msg += f"  ⚠️ Troppi segnali misti – fase di chop\n"
    if high_conf > total * 0.3:
        msg += f"  ✅ Buona percentuale di segnali ad alta confidence\n"
    
    msg += f"\n{'━' * 30}\n"
    msg += f"🤖 TradingAlertBot v{VERSION}\n"
    msg += f"⏰ {format_time_it()} ora italiana\n"
    
    state.setdefault("weekly_reports_sent", []).append(today_key)
    return msg

# ---------------------------------------------------------------------------
# MARKET SESSIONS
# ---------------------------------------------------------------------------
MARKET_SESSIONS = {
    "SYDNEY": {"name": "Sydney", "open_utc": 22, "close_utc": 7,
               "emoji": "🇦🇺", "assets": "AUD, NZD, JPY"},
    "TOKYO": {"name": "Tokyo", "open_utc": 0, "close_utc": 9,
              "emoji": "🇯🇵", "assets": "JPY, AUD, crypto"},
    "LONDRA": {"name": "Londra", "open_utc": 8, "close_utc": 17,
               "emoji": "🇬🇧", "assets": "EUR, GBP, ORO"},
    "NEW_YORK": {"name": "New York", "open_utc": 13, "close_utc": 22,
                 "emoji": "🇺🇸", "assets": "USD, S&P500, BTC"},
}

def check_sessions(state):
    now = datetime.now(timezone.utc)
    h_utc = now.hour
    m = now.minute
    alerts = []
    today = now.strftime("%Y%m%d")

    for key, ses in MARKET_SESSIONS.items():
        oh = ses["open_utc"]
        name = ses["name"]
        em = ses["emoji"]

        okey = f"open_{key}_{today}"
        if h_utc == oh and m < 5 and okey not in state.get("sessions", []):
            msg = f"🔔 {em} <b>Sessione {name} APERTA</b> | {ses['assets']}\n"
            alerts.append(msg)
            state.setdefault("sessions", []).append(okey)

    return alerts

# ---------------------------------------------------------------------------
# COHERENCE TRACKER (EXTRA)
# ---------------------------------------------------------------------------

def check_coherence(title, desc, crypto_prices):
    """Identifica pattern contrarian: news vs prezzo."""
    text_lower = f"{title} {desc}".lower()
    btc_change = crypto_prices.get("BTC", {}).get("change_24h", 0)
    
    # Keywords bullish/bearish nella news
    bull_kw = ["bullish", "surge", "rally", "approval", "adopt", "buy", "accumulate",
               "partnership", "launch", "record", "ath", "growth"]
    bear_kw = ["bearish", "crash", "dump", "reject", "ban", "hack", "sell",
               "panic", "collapse", "decline", "loss", "warning"]
    
    news_bull = sum(1 for kw in bull_kw if kw in text_lower)
    news_bear = sum(1 for kw in bear_kw if kw in text_lower)
    
    coherence = None
    
    # News bullish + prezzo giù = accumulo
    if news_bull > news_bear and btc_change < -2:
        coherence = "🔄 News bullish + prezzo giù → possibile ACCUMULO istituzionale"
    
    # News bearish + prezzo su = manipolazione
    elif news_bear > news_bull and btc_change > 2:
        coherence = "⚠️ News bearish + prezzo su → possibile MANIPOLAZIONE o short squeeze"
    
    # Panic + prezzo crolla = possibile bottom
    elif news_bear > 2 and btc_change < -5:
        coherence = "🩸 Panic estremo → possibile CAPITOLAZIONE (buy the dip?)"
    
    # Euforia + prezzo sale forte = possibile top
    elif news_bull > 2 and btc_change > 5:
        coherence = "🎪 Euforia + pump → possibile DISTRIBUTION TOP"
    
    return coherence

# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    log.info(f"TradingAlertBot v{VERSION} Decision Engine avviato")
    
    state = load_json(STATE_FILE, {"sessions": [], "fng_sent": [], "weekly_reports_sent": []})
    seen = load_json(SEEN_FILE, {"hashes": [], "last_clean": ""})
    price_history = load_json(PRICE_HISTORY_FILE, {})
    
    # Welcome message
    if not state.get(f"welcome_v{VERSION}"):
        welcome = (
            f"🧠 <b>TradingAlertBot v{VERSION} - DECISION ENGINE</b>\n"
            f"{'━' * 30}\n\n"
            f"🚀 Sistema completamente riscritto!\n\n"
            f"<b>Architettura multi-layer:</b>\n"
            f"📊 Event Engine (comprensione strutturata)\n"
            f"🔍 Causa → Effetto (no keyword semplici)\n"
            f"⚖️ Multi-Factor Scoring (3 orizzonti)\n"
            f"🧨 Smart Money Detector\n"
            f"🔄 Conflict Resolution\n"
            f"🧠 Market Phase Classifier\n"
            f"💡 Confidence System (1-5)\n"
            f"📉 Risk Filter anti-overtrading\n"
            f"📊 Forward Test Logging\n"
            f"📋 Report settimanale\n\n"
            f"<b>Regole:</b>\n"
            f"❌ Mai segnali assoluti senza conferme\n"
            f"❌ Mai inventare info non nella news\n"
            f"❌ Mai forzare BUY/SELL senza edge\n"
            f"✅ Sempre causa → effetto\n"
            f"✅ Sempre confidence prima di agire\n"
            f"✅ Smart money > prezzo\n\n"
            f"⏰ {format_time_it()} ora italiana\n"
        )
        send_telegram(welcome)
        state[f"welcome_v{VERSION}"] = True
        save_json(STATE_FILE, state)
        time.sleep(2)
    
    last_news = 0
    last_prices = 0
    last_sessions = 0
    cycle = 0
    
    while True:
        try:
            cycle += 1
            now_ts = time.time()
            now_utc = datetime.now(timezone.utc)
            log.info(f"--- Ciclo {cycle} ({format_time_it()}) ---")
            
            # Pulizia giornaliera
            today = now_utc.strftime("%Y-%m-%d")
            if seen.get("last_clean") != today:
                seen["hashes"] = seen["hashes"][-200:]
                seen["last_clean"] = today
                state["sessions"] = [s for s in state.get("sessions", []) if now_utc.strftime("%Y%m%d") in s]
                log.info("Pulizia giornaliera completata")
            
            # SESSIONI
            if now_ts - last_sessions >= SCAN_SESSIONS:
                alerts = check_sessions(state)
                for a in alerts:
                    send_telegram(a)
                    time.sleep(1)
                last_sessions = now_ts
            
            # PREZZI
            crypto_prices = {}
            commodities = {}
            if now_ts - last_prices >= SCAN_PRICES:
                crypto_prices = get_crypto_prices()
                commodities = get_commodity_prices()
                log.info(f"Prezzi: {len(crypto_prices)} crypto, {len(commodities)} commodities")
                last_prices = now_ts
            elif not crypto_prices:
                crypto_prices = get_crypto_prices()
                commodities = get_commodity_prices()
            
            # REPORT SETTIMANALE (domenica)
            weekly = generate_weekly_report(state)
            if weekly:
                send_telegram(weekly)
                save_json(STATE_FILE, state)
            
            # NOTIZIE (ogni 3 min)
            if now_ts - last_news >= SCAN_NEWS:
                fng_data = get_fear_greed()
                
                # ForexFactory
                ff = fetch_forexfactory()
                high_ff = [e for e in ff if "high" in str(e.get("impact", "")).lower()]
                ff_sent = 0
                for ev in high_ff[:3]:
                    h = news_hash(ev.get("title", ""), "FF")
                    if h not in seen["hashes"]:
                        # Formatta evento economico con Decision Engine
                        title = ev.get("title", "")
                        event = extract_event(title, "")
                        cause_effects = analyze_cause_effect(title)
                        scores = calculate_multi_factor_score(event, cause_effects, crypto_prices, fng_data)
                        
                        msg = f"📅 <b>EVENTO ECONOMICO</b>\n{'━' * 30}\n"
                        msg += f"📊 <b>{escape_html(translate(title))}</b>\n"
                        msg += f"💱 {escape_html(ev.get('currency', ''))} | Impatto: 🔴 ALTO\n"
                        msg += f"📁 Tipo: {event['type']}\n"
                        if cause_effects:
                            msg += f"→ {escape_html(cause_effects[0]['effect'])}\n"
                        msg += f"\n⏰ {format_time_it()} ora italiana\n"
                        
                        send_telegram(msg)
                        seen["hashes"].append(h)
                        ff_sent += 1
                        time.sleep(2)
                
                log.info(f"ForexFactory: {len(high_ff)} alto impatto, {ff_sent} nuovi")
                
                # Notizie RSS
                all_news = fetch_all_news()
                scored_news = []
                
                for item in all_news:
                    h = news_hash(item["title"], item["source"])
                    if h in seen["hashes"]:
                        continue
                    
                    full_text = f"{item['title']} {item.get('description', '')}"
                    
                    # Event Engine
                    event = extract_event(item["title"], item.get("description", ""))
                    
                    # Causa-Effetto
                    cause_effects = analyze_cause_effect(full_text)
                    
                    # Score per priorità (basato su attori + cause)
                    priority = event["max_actor_weight"] + len(cause_effects) * 2
                    
                    scored_news.append({
                        "item": item,
                        "hash": h,
                        "event": event,
                        "cause_effects": cause_effects,
                        "priority": priority
                    })
                
                # Ordina per priorità
                scored_news.sort(key=lambda x: -x["priority"])
                
                log.info(f"Notizie: {len(all_news)} totali, {len(scored_news)} nuove")
                
                sent = 0
                for news_data in scored_news:
                    if sent >= 3:  # Max 3 per ciclo (qualità > quantità)
                        break
                    
                    # Quiet hours: solo alta priorità
                    if is_quiet_hours() and news_data["priority"] < 5:
                        continue
                    
                    # Skip notizie a bassissima priorità
                    if news_data["priority"] < 2:
                        continue
                    
                    item = news_data["item"]
                    event = news_data["event"]
                    cause_effects = news_data["cause_effects"]
                    
                    # Multi-Factor Scoring
                    scores = calculate_multi_factor_score(event, cause_effects, crypto_prices, fng_data)
                    
                    # Smart Money Detection
                    btc_momentum = crypto_prices.get("BTC", {}).get("change_24h", 0)
                    price_dir = 1 if btc_momentum > 0 else (-1 if btc_momentum < 0 else 0)
                    smart_money = detect_smart_money(
                        f"{item['title']} {item.get('description', '')}",
                        event["actors"], price_dir
                    )
                    
                    # Market Phase
                    market_phase = classify_market_phase(crypto_prices, fng_data, smart_money)
                    
                    # Conflict Resolution
                    resolution = resolve_conflicts(scores, smart_money, market_phase)
                    
                    # Confidence
                    confidence = calculate_confidence(scores, smart_money, resolution, event["actors"])
                    
                    # Risk Filter
                    risk_filter = apply_risk_filter(confidence, resolution)
                    
                    # Assets
                    assets = find_affected_assets(f"{item['title']} {item.get('description', '')}")
                    
                    # Coherence check
                    coherence = check_coherence(item["title"], item.get("description", ""), crypto_prices)
                    
                    # Format output
                    msg = format_decision_output(
                        item, event, cause_effects, scores, smart_money,
                        market_phase, resolution, confidence, risk_filter,
                        assets, {**crypto_prices, **commodities}
                    )
                    
                    # Aggiungi coherence se presente
                    if coherence:
                        msg += f"\n{coherence}\n"
                    
                    # Validate (no hallucination)
                    warnings = validate_analysis(item["title"], item.get("description", ""),
                                                cause_effects, event["actors"])
                    if warnings:
                        log.info(f"Validation warnings: {warnings}")
                    
                    # Invia
                    send_telegram(msg)
                    seen["hashes"].append(news_data["hash"])
                    
                    # Forward test log
                    log_forward_test(item, resolution, confidence, scores, crypto_prices, assets)
                    
                    sent += 1
                    time.sleep(3)
                
                log.info(f"Notizie inviate: {sent}")
                
                # Salva stato
                save_json(SEEN_FILE, seen)
                save_json(STATE_FILE, state)
                last_news = now_ts
            
            log.info(f"Ciclo {cycle} OK. Prossimo tra {LOOP_SLEEP}s")
            time.sleep(LOOP_SLEEP)
            
        except Exception as e:
            log.error(f"Errore ciclo {cycle}: {e}")
            log.error(traceback.format_exc())
            time.sleep(60)

if __name__ == "__main__":
    main()
