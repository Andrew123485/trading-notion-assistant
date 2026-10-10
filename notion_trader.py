import os
from dotenv import load_dotenv
load_dotenv()
import requests
import json
import re
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOKEN = os.getenv("NOTION_TOKEN", "YOUR_NOTION_TOKEN")

NOTION_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

NOTION_PROXIES = {
    "http": "http://127.0.0.1:10808",
    "https": "http://127.0.0.1:10808"
}

def notion_api_request(method, url, json_payload=None, max_retries=5):
    """Safe Notion API caller with HTTP CONNECT tunnel to avoid PySocks EOF drops."""
    for attempt in range(max_retries):
        try:
            kwargs = {"headers": NOTION_HEADERS, "timeout": 30}
            if NOTION_PROXIES:
                kwargs["proxies"] = NOTION_PROXIES
            if method.upper() == "POST":
                resp = requests.post(url, json=json_payload, **kwargs)
            elif method.upper() == "PATCH":
                resp = requests.patch(url, json=json_payload, **kwargs)
            else:
                resp = requests.get(url, params=json_payload, **kwargs)
            return resp
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise e

# Database IDs
DB_BACKTEST_ID = "23be9b3ef90a812486c6daf394bdb91d"
DB_MISSED_ID = "660e9b3ef90a83cea4cb010e1caa20e5"
DB_REAL_ID = "220e9b3ef90a818cab6cc2ec31f07639"
DB_ID = DB_BACKTEST_ID

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

def load_lookups(filename="relation_lookups.json"):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

LOOKUPS_BACKTEST = load_lookups("relation_lookups.json")
LOOKUPS_MISSED = load_lookups("missed_relation_lookups.json")
LOOKUPS_REAL = load_lookups("real_relation_lookups.json")
LOOKUPS = LOOKUPS_BACKTEST

from pathlib import Path
RULES_PATH = Path(__file__).parent / "trading_ai_rules.json"

def load_trading_rules():
    if RULES_PATH.exists():
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading rules: {e}")
    return {}

TRADING_RULES = load_trading_rules()

def get_account_balance(mode="real", manual_balance=None):
    if manual_balance is not None:
        try:
            return round(float(manual_balance), 2)
        except Exception:
            pass
    base = TRADING_RULES.get("account", {}).get("base_deposit", 10000.0)
    db_id = DB_REAL_ID if mode == "real" else DB_BACKTEST_ID
    try:
        url = f"https://api.notion.com/v1/databases/{db_id}/query"
        payload = {"page_size": 100}
        res = notion_api_request("POST", url, payload)
        if res.status_code == 200:
            results = res.json().get("results", [])
            total_pnl = 0.0
            for r in results:
                p = r.get("properties", {})
                pnl_v = p.get("PNL", {}).get("number")
                if pnl_v is None:
                    pnl_v = p.get("PNL Amarkets", {}).get("number")
                if pnl_v is not None:
                    total_pnl += float(pnl_v)
            return round(base + total_pnl, 2)
    except Exception as e:
        print(f"Error getting account balance: {e}")
    return base

def get_recommended_risk(balance=None, mode="real", manual_balance=None):
    if balance is None:
        balance = get_account_balance(mode=mode, manual_balance=manual_balance)
    tiers = TRADING_RULES.get("account", {}).get("risk_management", {}).get("tiers", [])
    for tier in tiers:
        min_b = tier.get("min_balance", -float("inf"))
        max_b = tier.get("max_balance", float("inf"))
        if min_b <= balance <= max_b:
            return tier.get("risk_label", "1%"), tier.get("risk_percent", 1.0), balance, tier.get("status", "")
    return "1%", 1.0, balance, "Normal"

CANONICAL_REL_NAMES = {
    "tf": "Entry", "timeframe": "Entry", "тф": "Entry", "entry": "Entry",
    "dir": "Direction", "direction": "Direction", "направление": "Direction",
    "acc": "Счет", "account": "Счет", "счет": "Счет",
    "session": "Session", "сессия": "Session",
    "year": "Year", "год": "Year",
    "asset": "Assets", "assets": "Assets", "пара": "Assets",
    "model": "POI", "poi": "POI", "модель": "POI",
    "month": "Month", "месяц": "Month",
    "errors": "Пометки", "пометки": "Пометки", "ошибки": "Пометки"
}

ASSET_ALIASES = {
    "UK100": "US100",
    "FTSE": "US100",
    "FTSE100": "US100",
    "NAS100": "US100",
    "NDX": "US100",
    "USTEC": "US100",
    "DAX": "GER40",
    "GOLD": "XAU",
    "SP500": "US500",
    "S&P500": "US500",
    "SPX": "US500",
    "SPX500": "US500",
    "SPX500USD": "US500",
    "CP500": "US500",
    "ES": "US500",
}

def get_relation_id(relation_name, key, mode="backtest"):
    if not key:
        return None
    if mode == "missed":
        lookups = LOOKUPS_MISSED
    elif mode == "real":
        lookups = LOOKUPS_REAL
    else:
        lookups = LOOKUPS_BACKTEST

    canon = CANONICAL_REL_NAMES.get(relation_name.lower(), relation_name)
    if canon not in lookups:
        if relation_name in lookups:
            canon = relation_name
        else:
            return None

    items = lookups[canon].get("items", {})
    # Exact match
    if key in items:
        return items[key]
    # Check aliases for Assets (e.g. UK100 -> US100 for backtests)
    if canon == "Assets" and str(key).upper() in ASSET_ALIASES:
        target_asset = ASSET_ALIASES[str(key).upper()]
        if target_asset in items:
            return items[target_asset]
    # Case insensitive match
    for k, v in items.items():
        if k.strip().lower() == str(key).strip().lower():
            return v
    # Substring match
    for k, v in items.items():
        if str(key).strip().lower() in k.strip().lower() or k.strip().lower() in str(key).strip().lower():
            return v
    return None

def get_next_trade_number(year_str=None, month_str=None, mode="backtest"):
    """Query recent trades for the specified Year and Month to determine the next trade number."""
    if mode == "missed":
        db_id = DB_MISSED_ID
    elif mode == "real":
        db_id = DB_REAL_ID
    else:
        db_id = DB_BACKTEST_ID

    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    filters_list = []
    if year_str:
        y_id = get_relation_id("Year", year_str, mode=mode)
        if y_id:
            filters_list.append({"property": "Year", "relation": {"contains": y_id}})
    if month_str:
        m_id = get_relation_id("Month", month_str, mode=mode)
        if m_id:
            filters_list.append({"property": "Month", "relation": {"contains": m_id}})

    payload = {"page_size": 100}
    if len(filters_list) == 1:
        payload["filter"] = filters_list[0]
    elif len(filters_list) > 1:
        payload["filter"] = {"and": filters_list}

    max_num = 0
    try:
        res = notion_api_request("POST", url, json_payload=payload)
        if res.status_code == 200:
            data = res.json()
            for page in data.get("results", []):
                title_prop = page.get("properties", {}).get("№", {}).get("title", [])
                text = "".join(t.get("plain_text", "") for t in title_prop).strip()
                m = re.search(r"(\d+)", text)
                if m:
                    num = int(m.group(1))
                    if num > max_num:
                        max_num = num
        else:
            print(f"Error querying Notion db ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Error finding max trade number ({mode}): {e}")
    return max_num + 1 if max_num > 0 else 1

def normalize_rr(raw_val: float) -> float:
    """Fix Whisper errors like '244' instead of 2.44 or '25' instead of 2.5."""
    if raw_val >= 100 and raw_val < 1000:
        return round(raw_val / 100.0, 2)
    elif raw_val >= 20 and raw_val < 100:
        return round(raw_val / 10.0, 2)
    return round(raw_val, 2)

def parse_risk_val(risk_input) -> float:
    """Extracts numeric risk percentage e.g. '0.5%' -> 0.5, '1.3%' -> 1.3, 1 -> 1.0"""
    if risk_input is None:
        return 1.0
    if isinstance(risk_input, (int, float)):
        return float(risk_input)
    s = str(risk_input).replace("%", "").replace(",", ".").strip()
    try:
        val = float(s)
        return val if val > 0 else 1.0
    except Exception:
        return 1.0

def compute_effective_rr_and_pnl(raw_rr: float, risk_input, pnl_dollar: float = None, deposit: float = 10000.0):
    """
    Computes effective RR and dollar PNL based on trade risk and account deposit ($10,000 base).
    Example: raw_rr=1.75, risk='1.3%' -> effective_rr=2.28, pnl=227.50
             raw_rr=-1.0, risk='0.5%' -> effective_rr=-0.5, pnl=-50.00
    """
    r_float = parse_risk_val(risk_input)
    effective_rr = round(float(raw_rr) * (r_float / 1.0) + 1e-9, 2)
    risk_dollars = deposit * (r_float / 100.0)
    if pnl_dollar is None:
        final_pnl = round(float(raw_rr) * risk_dollars, 2)
    else:
        final_pnl = float(pnl_dollar)
    return effective_rr, final_pnl, r_float

def get_tv_image_url(tv_link: str) -> str:
    """Converts a TradingView snapshot webpage link (https://www.tradingview.com/x/CODE/)
    into a direct S3 image snapshot URL (https://s3.tradingview.com/snapshots/c/CODE.png)."""
    if not tv_link:
        return ""
    if tv_link.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return tv_link
    m = re.search(r"tradingview\.com/x/([A-Za-z0-9]+)", tv_link)
    if m:
        code = m.group(1)
        return f"https://s3.tradingview.com/snapshots/{code[0].lower()}/{code}.png"
    return tv_link

def get_tv_asset(tv_link: str) -> str:
    """Fetches the TradingView snapshot page and extracts the ticker/symbol from the title."""
    if not tv_link:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for attempt in range(3):
        try:
            resp = requests.get(tv_link, headers=headers, timeout=10)
            if resp.status_code == 200:
                m = re.search(r'(?:property="og:title"\s+content="|<title>)(?:[A-Za-z0-9_]+:)?([A-Za-z0-9]+)\s+Chart\s+Image', resp.text, re.I)
                if m:
                    return m.group(1).upper()
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    return ""

def append_chart_blocks(page_id: str, htf_1h: str = None, ltf_3m: str = None, htf_4h: str = None, after_url: str = None):
    """Appends heading and image blocks to a Notion page body so charts are displayed visually inside the page."""
    if not page_id:
        return
    # Check if page already has image blocks to avoid duplicates
    try:
        check_res = notion_api_request("GET", f"https://api.notion.com/v1/blocks/{page_id}/children")
        if check_res.status_code == 200:
            existing = [b.get("type") for b in check_res.json().get("results", [])]
            if "image" in existing:
                return
    except Exception as e:
        print(f"⚠️ Не удалось проверить существующие блоки: {e}")

    blocks = []
    if htf_1h:
        img_url = get_tv_image_url(htf_1h)
        blocks.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "📊 Старший таймфрейм 1H (HTF)"}}]
                }
            },
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": img_url}
                }
            }
        ])
    if ltf_3m:
        img_url = get_tv_image_url(ltf_3m)
        blocks.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "🎯 Младший таймфрейм 3M / 1M (Entry / BoS)"}}]
                }
            },
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": img_url}
                }
            }
        ])
    if htf_4h:
        img_url = get_tv_image_url(htf_4h)
        blocks.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "📈 Старший таймфрейм 4H"}}]
                }
            },
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": img_url}
                }
            }
        ])
    if after_url:
        img_url = get_tv_image_url(after_url)
        blocks.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "🏁 Результат сделки (After)"}}]
                }
            },
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": img_url}
                }
            }
        ])

    if not blocks:
        return

    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    try:
        res = notion_api_request("PATCH", url, json_payload={"children": blocks})
        if res.status_code == 200:
            print(f"✅ В тело страницы {page_id} успешно добавлены блоки с графиками.")
        else:
            print(f"⚠️ Ошибка добавления блоков в Notion ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"⚠️ Исключение при добавлении блоков графиков: {e}")

def add_trade(
    trade_num=None,
    asset="EURUSD",
    date_str=None,           # 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'
    direction="Long",        # 'Long' or 'Short'
    trend=None,              # 'uptrend' or 'downtrend'
    session=None,            # 'London', 'Frankfurt', 'NY', 'Asia'
    timeframe="M3",          # 'M3', 'M1', 'M5'
    model="frankfurt manipulation",
    transaction_type="Continuation",
    rr=0.0,
    htf_1h_url=None,
    ltf_3m_url=None,
    htf_4h_url=None,
    after_url=None,
    target_tags=None,
    entry_zone_tags=None,
    correct_tags=None,
    pometki=None,
    improve_tags=None,
    notes="",
    reasoning="",
    anxiety=False,
    account=None,
    pnl_dollar=None,
    risk="1%",
    mistakes="",
    comments="",
    mode="backtest"
):
    dt = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                pass
    if not dt:
        dt = datetime.now()

    if dt.hour != 0 or dt.minute != 0:
        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%S+03:00")
    else:
        iso_date = dt.strftime("%Y-%m-%d")
    year_str = str(dt.year)
    month_str = MONTH_NAMES_RU.get(dt.month, "Январь")

    if trade_num is None:
        trade_num = f"{get_next_trade_number(year_str=year_str, month_str=month_str, mode=mode)}."
    elif isinstance(trade_num, int):
        trade_num = f"{trade_num}."
    elif not str(trade_num).endswith("."):
        trade_num = f"{trade_num}."

    # Auto-detect session if not given but hour is known
    if not session and dt.hour != 0:
        h = dt.hour
        if 8 <= h < 11:
            session = "Frankfurt"
        elif 11 <= h < 16:
            session = "London"
        elif 16 <= h < 24:
            session = "NY"
        else:
            session = "Asia"
    elif not session:
        session = "London"

    if mode == "missed":
        target_db = DB_MISSED_ID
    elif mode == "real":
        target_db = DB_REAL_ID
    else:
        target_db = DB_BACKTEST_ID

    if account is None:
        account = "AMarkets" if mode in ("missed", "real") else "AMarkets Demo"

    # Relations using unified names
    asset_id = get_relation_id("Assets", asset, mode=mode)
    dir_id = get_relation_id("Direction", direction, mode=mode)
    year_id = get_relation_id("Year", year_str, mode=mode)
    month_id = get_relation_id("Month", month_str, mode=mode)
    session_id = get_relation_id("Session", session, mode=mode)
    tf_id = get_relation_id("Entry", timeframe, mode=mode)
    model_id = get_relation_id("POI", model if isinstance(model, str) else (model[0] if model else None), mode=mode)
    acc_id = get_relation_id("Счет", account, mode=mode)

    effective_rr, final_pnl, r_float = compute_effective_rr_and_pnl(rr, risk, pnl_dollar=pnl_dollar)
    r_str = f"{r_float:g}%"

    # Identical properties for databases
    props = {
        "№": {"title": [{"text": {"content": str(trade_num)}}]},
        "Data": {"date": {"start": iso_date}},
        "RR": {"number": effective_rr},
        "Profitable": {"checkbox": bool(effective_rr > 0.05)},
        "Reached_the_first_target": {"checkbox": bool(effective_rr > 1.5)},
        "Reached_the_second_target": {"checkbox": bool(effective_rr >= 2.0)},
        "3R ": {"checkbox": bool(effective_rr >= 3.0)},
        "4R": {"checkbox": bool(effective_rr >= 4.0)},
        "5R": {"checkbox": bool(effective_rr >= 5.0)},
        "Чувствую ли я тревожность?": {"checkbox": bool(anxiety)},
        "PNL": {"number": float(final_pnl)},
        "Risk": {"select": {"name": r_str}},
    }

    if mode in ("real", "missed"):
        props["PNL Amarkets"] = {"number": float(final_pnl)}
        props["RR Amarkets"] = {"number": effective_rr}

    if asset_id: props["Assets"] = {"relation": [{"id": asset_id}]}
    if dir_id: props["Direction"] = {"relation": [{"id": dir_id}]}
    if year_id: props["Year"] = {"relation": [{"id": year_id}]}
    if month_id: props["Month"] = {"relation": [{"id": month_id}]}
    if session_id: props["Session"] = {"relation": [{"id": session_id}]}
    if tf_id: props["Entry"] = {"relation": [{"id": tf_id}]}
    model_rel = []
    if isinstance(model, list):
        for m_item in model:
            mid = get_relation_id("POI", m_item, mode=mode)
            if mid and {"id": mid} not in model_rel:
                model_rel.append({"id": mid})
    elif isinstance(model, str):
        for m_item in model.split("+"):
            m_item = m_item.strip()
            mid = get_relation_id("POI", m_item, mode=mode)
            if mid and {"id": mid} not in model_rel:
                model_rel.append({"id": mid})
    if model_rel:
        props["POI"] = {"relation": model_rel}

    if acc_id: props["Счет"] = {"relation": [{"id": acc_id}]}

    if mistakes:
        props["Ошибки"] = {"rich_text": [{"type": "text", "text": {"content": mistakes}}]}
    if comments:
        props["Комментарии"] = {"rich_text": [{"type": "text", "text": {"content": comments}}]}

    if trend:
        props["Trend"] = {"multi_select": [{"name": trend}]}
    elif direction == "Long":
        props["Trend"] = {"multi_select": [{"name": "uptrend"}]}
    elif direction == "Short":
        props["Trend"] = {"multi_select": [{"name": "downtrend"}]}

    if transaction_type:
        props["Type_of_transaction"] = {"select": {"name": transaction_type}}

    if htf_1h_url:
        props["HTF_1h"] = {"url": htf_1h_url}
        img_1h = get_tv_image_url(htf_1h_url)
        props["Screenshot_1h"] = {
            "files": [{"name": "HTF_1h.png", "type": "external", "external": {"url": img_1h}}]
        }
    if ltf_3m_url:
        props["LTF_3m"] = {"url": ltf_3m_url}
        img_3m = get_tv_image_url(ltf_3m_url)
        props["Screenshot_3m"] = {
            "files": [{"name": "LTF_3m.png", "type": "external", "external": {"url": img_3m}}]
        }
    if htf_4h_url:
        props["HTF_4h"] = {"url": htf_4h_url}
    if after_url:
        props["After"] = {"url": after_url}

    if target_tags:
        props["Target"] = {"multi_select": [{"name": t} for t in target_tags]}
    if entry_zone_tags:
        props["Entry zone"] = {"multi_select": [{"name": t} for t in entry_zone_tags]}
    if correct_tags:
        props["Сделал правильно"] = {"multi_select": [{"name": t} for t in correct_tags]}
    if improve_tags:
        props["Что улучшить"] = {"multi_select": [{"name": t} for t in improve_tags]}

    if pometki:
        pometki_ids = []
        for p in pometki:
            pid = get_relation_id("Пометки", p, mode=mode)
            if pid:
                pometki_ids.append({"id": pid})
        if pometki_ids:
            props["Пометки"] = {"relation": pometki_ids}

    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": str(notes)}}]}

    payload = {
        "parent": {"database_id": target_db},
        "properties": props
    }

    url = "https://api.notion.com/v1/pages"
    try:
        res = notion_api_request("POST", url, json_payload=payload)
        if res.status_code == 200:
            result = res.json()
            page_url = result.get("url")
            page_id = result.get("id")
            print(f"✅ Успешно добавлена сделка ({mode}) {trade_num}: {page_url}")
            if htf_1h_url or ltf_3m_url or htf_4h_url or after_url:
                append_chart_blocks(page_id, htf_1h=htf_1h_url, ltf_3m=ltf_3m_url, htf_4h=htf_4h_url, after_url=after_url)
            final_trend = trend if trend else ("uptrend" if direction == "Long" else "downtrend")
            return {
                "success": True,
                "page_id": page_id,
                "url": page_url,
                "trade_num": trade_num,
                "mode": mode,
                "asset": asset,
                "year": year_str,
                "month": month_str,
                "date": iso_date,
                "direction": direction,
                "trend": final_trend,
                "transaction_type": transaction_type,
                "model": " + ".join(model) if isinstance(model, list) else str(model),
                "entry": timeframe,
                "rr": effective_rr,
                "raw_rr": float(rr),
                "pnl_dollar": final_pnl,
                "notes": notes,
                "risk": r_str,
                "mistakes": mistakes,
                "comments": comments
            }
        else:
            print(f"❌ Ошибка добавления в Notion ({res.status_code}): {res.text}")
            return {"success": False, "error": res.text}
    except Exception as e:
        print(f"❌ Исключение добавления в Notion: {e}")
        return {"success": False, "error": str(e)}


def update_trade_links(page_id: str, links: list):
    """Updates chart links and screenshot file attachments on an existing Notion trade page."""
    htf_1h = None
    ltf_3m = None
    htf_4h = None
    if len(links) >= 1:
        htf_1h = links[0]
    if len(links) >= 2:
        ltf_3m = links[1]
    if len(links) >= 3:
        htf_4h = links[2]

    props = {}
    if htf_1h:
        props["HTF_1h"] = {"url": htf_1h}
        img_1h = get_tv_image_url(htf_1h)
        props["Screenshot_1h"] = {
            "files": [{"name": "HTF_1h.png", "type": "external", "external": {"url": img_1h}}]
        }
    if ltf_3m:
        props["LTF_3m"] = {"url": ltf_3m}
        img_3m = get_tv_image_url(ltf_3m)
        props["Screenshot_3m"] = {
            "files": [{"name": "LTF_3m.png", "type": "external", "external": {"url": img_3m}}]
        }
    if htf_4h:
        props["HTF_4h"] = {"url": htf_4h}

    if not props:
        return {"success": False, "error": "No links provided"}

    url = f"https://api.notion.com/v1/pages/{page_id}"
    res = notion_api_request("PATCH", url, json_payload={"properties": props})
    if res.status_code == 200:
        append_chart_blocks(page_id, htf_1h=htf_1h, ltf_3m=ltf_3m, htf_4h=htf_4h)
        return {"success": True, "page_id": page_id, "props": props}
    return {"success": False, "error": res.text}


def get_most_recent_trade_page(mode="real"):
    """Fetches the most recently created or dated trade page from Notion database."""
    if mode == "missed":
        db_id = DB_MISSED_ID
    elif mode == "real":
        db_id = DB_REAL_ID
    else:
        db_id = DB_BACKTEST_ID

    payload = {
        "page_size": 1,
        "sorts": [{"timestamp": "created_time", "direction": "descending"}]
    }
    try:
        res = notion_api_request("POST", f"https://api.notion.com/v1/databases/{db_id}/query", json_payload=payload)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                p = results[0]
                props = p.get("properties", {})
                num_title = props.get("№", {}).get("title", [])
                trade_num = num_title[0].get("plain_text", "1.") if num_title else "1."
                return {
                    "page_id": p.get("id"),
                    "url": p.get("url", f"https://app.notion.com/p/{p.get('id').replace('-', '')}"),
                    "trade_num": trade_num,
                    "mode": mode
                }
    except Exception as e:
        print(f"Error getting recent trade page: {e}")
    return None


def update_trade_properties(page_id: str, updates: dict, mode: str = "real"):
    """
    Directly updates properties of an existing Notion trade page.
    Handles relations, dates, text, numbers, multi_selects properly.
    """
    patch_props = {}

    # Trade Number
    if "trade_num" in updates and updates["trade_num"]:
        t_num = str(updates["trade_num"])
        if not t_num.endswith("."):
            t_num += "."
        patch_props["№"] = {"title": [{"text": {"content": t_num}}]}

    # Date / Year / Month
    if "date" in updates and updates["date"]:
        d_val = updates["date"]
        patch_props["Data"] = {"date": {"start": d_val}}
        try:
            dt_obj = datetime.strptime(d_val[:10], "%Y-%m-%d")
            y_name = str(dt_obj.year)
            m_name = MONTH_NAMES_RU.get(dt_obj.month, "Январь")
            y_id = get_relation_id("Year", y_name, mode=mode)
            if y_id:
                patch_props["Year"] = {"relation": [{"id": y_id}]}
            m_id = get_relation_id("Month", m_name, mode=mode)
            if m_id:
                patch_props["Month"] = {"relation": [{"id": m_id}]}
        except Exception as e:
            print(f"Date rel update error: {e}")

    # Asset
    if "asset" in updates and updates["asset"]:
        raw_asset = updates["asset"]
        canon_asset = ASSET_ALIASES.get(raw_asset.upper(), raw_asset)
        asset_id = get_relation_id("Assets", canon_asset, mode=mode)
        if asset_id:
            patch_props["Assets"] = {"relation": [{"id": asset_id}]}

    # Direction
    if "direction" in updates and updates["direction"]:
        dir_val = updates["direction"]
        dir_id = get_relation_id("Direction", dir_val, mode=mode)
        if dir_id:
            patch_props["Direction"] = {"relation": [{"id": dir_id}]}

    # Session
    if "session" in updates and updates["session"]:
        sess_val = updates["session"]
        sess_id = get_relation_id("Session", sess_val, mode=mode)
        if sess_id:
            patch_props["Session"] = {"relation": [{"id": sess_id}]}

    # Entry TF
    if "entry" in updates and updates["entry"]:
        entry_val = updates["entry"]
        entry_id = get_relation_id("Entry", entry_val, mode=mode)
        if entry_id:
            patch_props["Entry"] = {"relation": [{"id": entry_id}]}

    # POI / Model
    if "poi" in updates and updates["poi"]:
        poi_vals = updates["poi"]
        if isinstance(poi_vals, str):
            poi_vals = [p.strip() for p in poi_vals.split("+")]
        poi_rel = []
        for p_name in poi_vals:
            pid = get_relation_id("POI", p_name, mode=mode)
            if pid and {"id": pid} not in poi_rel:
                poi_rel.append({"id": pid})
        if poi_rel:
            patch_props["POI"] = {"relation": poi_rel}

    # Risk %
    r_val = updates.get("risk")
    r_float = parse_risk_val(r_val) if r_val else None
    if r_val:
        patch_props["Risk"] = {"select": {"name": f"{r_float:g}%"}}

    # RR & PNL calculation
    if "rr" in updates and updates["rr"] is not None:
        try:
            raw_rr = float(updates["rr"])
            effective_risk = r_float if r_float is not None else 1.0
            effective_rr, calc_pnl, _ = compute_effective_rr_and_pnl(
                raw_rr,
                effective_risk,
                pnl_dollar=updates.get("pnl_dollar")
            )
            patch_props["RR"] = {"number": effective_rr}
            if mode in ("real", "missed"):
                patch_props["RR Amarkets"] = {"number": effective_rr}

            patch_props["Profitable"] = {"checkbox": bool(effective_rr > 0.05)}
            patch_props["Reached_the_first_target"] = {"checkbox": bool(effective_rr > 1.5)}
            patch_props["Reached_the_second_target"] = {"checkbox": bool(effective_rr >= 2.0)}
            patch_props["3R "] = {"checkbox": bool(effective_rr >= 3.0)}
            patch_props["4R"] = {"checkbox": bool(effective_rr >= 4.0)}
            patch_props["5R"] = {"checkbox": bool(effective_rr >= 5.0)}

            pnl_col = "PNL Amarkets" if mode == "real" else "PNL"
            patch_props[pnl_col] = {"number": calc_pnl}
            patch_props["PNL"] = {"number": calc_pnl}
        except Exception:
            pass
    elif "pnl_dollar" in updates and updates["pnl_dollar"] is not None:
        try:
            pnl_f = float(updates["pnl_dollar"])
            pnl_col = "PNL Amarkets" if mode == "real" else "PNL"
            patch_props[pnl_col] = {"number": pnl_f}
            patch_props["PNL"] = {"number": pnl_f}
        except Exception:
            pass

    # Пометки / Mistakes tags
    if "pometki" in updates and updates["pometki"]:
        pom_vals = updates["pometki"]
        if isinstance(pom_vals, str):
            pom_vals = [p.strip() for p in pom_vals.split(",")]
        pom_rel = []
        for p_name in pom_vals:
            pid = get_relation_id("Пометки", p_name, mode=mode)
            if pid and {"id": pid} not in pom_rel:
                pom_rel.append({"id": pid})
        if pom_rel:
            patch_props["Пометки"] = {"relation": pom_rel}

    # PNL Dollar
    if "pnl_dollar" in updates and updates["pnl_dollar"] is not None:
        try:
            pnl_f = float(updates["pnl_dollar"])
            pnl_col = "PNL Amarkets" if mode == "real" else "PNL"
            patch_props[pnl_col] = {"number": pnl_f}
        except Exception:
            pass

    # Target
    if "targets" in updates and updates["targets"]:
        tgt_vals = updates["targets"]
        if isinstance(tgt_vals, str):
            tgt_vals = [tgt_vals]
        patch_props["Target"] = {"multi_select": [{"name": t} for t in tgt_vals]}

    # Notes
    if "notes" in updates and updates["notes"]:
        patch_props["Notes"] = {
            "rich_text": [{"type": "text", "text": {"content": updates["notes"]}}]
        }

    # Что улучшить / Mistakes
    if "improve_tags" in updates and updates["improve_tags"]:
        imp_vals = updates["improve_tags"]
        if isinstance(imp_vals, str):
            imp_vals = [imp_vals]
        patch_props["Что улучшить"] = {"multi_select": [{"name": t} for t in imp_vals]}

    if not patch_props:
        return {"success": False, "error": "No properties to update"}

    url = f"https://api.notion.com/v1/pages/{page_id}"
    res = notion_api_request("PATCH", url, json_payload={"properties": patch_props})
    if res.status_code == 200:
        return {"success": True, "page_id": page_id, "updated_props": list(patch_props.keys())}
    else:
        return {"success": False, "error": res.text, "status_code": res.status_code}


def get_trade_page_details(page_id: str, mode: str = "real"):
    """Fetches a trade page by ID and extracts high-level trade attributes."""
    if mode == "missed":
        lookups = LOOKUPS_MISSED
    elif mode == "real":
        lookups = LOOKUPS_REAL
    else:
        lookups = LOOKUPS_BACKTEST
    id_to_name = {item_id: name for cat, cdata in lookups.items() for name, item_id in cdata.get("items", {}).items()}

    res = notion_api_request("GET", f"https://api.notion.com/v1/pages/{page_id}")
    if res.status_code != 200:
        return None
    page = res.json()
    props = page.get("properties", {})
    num_title = props.get("№", {}).get("title", [])
    num = num_title[0].get("plain_text", "") if num_title else ""
    rr_val = props.get("RR", {}).get("number")
    dt = (props.get("Data", {}).get("date") or {}).get("start", "")

    poi_ids = [x["id"] for x in props.get("POI", {}).get("relation", [])]
    poi_names = [id_to_name.get(pid) for pid in poi_ids if pid in id_to_name]

    entry_ids = [x["id"] for x in props.get("Entry", {}).get("relation", [])]
    entry = id_to_name.get(entry_ids[0], "M3") if entry_ids else "M3"

    dir_ids = [x["id"] for x in props.get("Direction", {}).get("relation", [])]
    direction = id_to_name.get(dir_ids[0], "Long") if dir_ids else "Long"

    asset_ids = [x["id"] for x in props.get("Assets", {}).get("relation", [])]
    asset = id_to_name.get(asset_ids[0], "EURUSD") if asset_ids else "EURUSD"

    session_ids = [x["id"] for x in props.get("Session", {}).get("relation", [])]
    session = id_to_name.get(session_ids[0], "London") if session_ids else "London"

    htf_1h = (props.get("HTF_1h", {}) or {}).get("url") or ""
    ltf_3m = (props.get("LTF_3m", {}) or {}).get("url") or ""
    htf_4h = (props.get("HTF_4h", {}) or {}).get("url") or ""
    after_url = (props.get("After", {}) or {}).get("url") or ""

    notes = "".join([t.get("plain_text", "") for t in props.get("Notes", {}).get("rich_text", [])])
    mistakes = "".join([t.get("plain_text", "") for t in props.get("Ошибки", {}).get("rich_text", [])])
    comments = "".join([t.get("plain_text", "") for t in props.get("Комментарии", {}).get("rich_text", [])])
    risk_sel = (props.get("Risk", {}).get("select") or {}).get("name", "1%")

    target_list = [t.get("name", "") for t in props.get("Target", {}).get("multi_select", [])]
    pnl_val = props.get("PNL", {}).get("number")
    if pnl_val is None:
        pnl_val = props.get("PNL Amarkets", {}).get("number")

    return {
        "num": num,
        "date": dt,
        "rr": rr_val,
        "pnl": pnl_val,
        "asset": asset,
        "poi": poi_names,
        "entry": entry,
        "direction": direction,
        "session": session,
        "htf_1h": htf_1h,
        "ltf_3m": ltf_3m,
        "htf_4h": htf_4h,
        "after_url": after_url,
        "notes": notes,
        "mistakes": mistakes,
        "comments": comments,
        "risk": risk_sel,
        "targets": target_list,
        "url": page.get("url", "")
    }


def adapt_poi_for_mode(poi_names, target_mode):
    if not poi_names:
        return ["Frankfurt 1h3m" if target_mode == "real" else "frankfurt manipulation"]
    if isinstance(poi_names, str):
        poi_names = [p.strip() for p in poi_names.split("+")]
    adapted = []
    for p in poi_names:
        p_low = p.lower()
        if "frank" in p_low or "манипуляц" in p_low:
            adapted.append("Frankfurt 1h3m" if target_mode == "real" else "frankfurt manipulation")
        elif "4h" in p_low and "fvg" in p_low:
            adapted.append("fvg 4h" if target_mode == "real" else "Fvg 4h")
        elif "sweep" in p_low and "bos" in p_low:
            adapted.append("Sweep+Bos/fvg" if target_mode == "real" else "Sweep+Bos")
        elif "1h" in p_low and "fvg" in p_low:
            adapted.append("Fvg 1h")
        elif "bullet" in p_low or "silver" in p_low:
            adapted.append("silver bullet")
        elif "pdh" in p_low or "pdl" in p_low:
            adapted.append("снятие PDH/PDL" if target_mode == "real" else p)
        else:
            adapted.append(p)
    return adapted


def adapt_targets_for_mode(targets, target_mode):
    if not targets:
        return None
    adapted = []
    for t in targets:
        if "pd" in t.lower():
            adapted.append("PDH/PDL" if target_mode == "real" else "PDL/PDH")
        else:
            adapted.append(t)
    return adapted


def move_trade_to_mode(page_id: str, from_mode: str, to_mode: str, extra_updates: dict = None):
    """
    Moves an existing trade from one Notion database to another
    (e.g. from real to backtest or vice versa).
    """
    if extra_updates is None:
        extra_updates = {}

    details = get_trade_page_details(page_id, mode=from_mode)
    if not details:
        return {"success": False, "error": f"Не удалось загрузить данные сделки {page_id}"}

    date_str = extra_updates.get("date") or details.get("date") or datetime.now().strftime("%Y-%m-%d")
    asset = extra_updates.get("asset") or details.get("asset") or "US500"
    direction = extra_updates.get("direction") or details.get("direction") or "Long"
    session = extra_updates.get("session") or details.get("session") or "London"
    entry = extra_updates.get("entry") or details.get("entry") or "M3"
    poi = extra_updates.get("poi") or details.get("poi") or ["frankfurt manipulation"]
    rr = extra_updates.get("rr") if extra_updates.get("rr") is not None else details.get("rr", 0.0)
    risk = extra_updates.get("risk") or details.get("risk") or "1%"
    pnl = extra_updates.get("pnl_dollar") if extra_updates.get("pnl_dollar") is not None else details.get("pnl")
    notes = extra_updates.get("notes") or details.get("notes") or ""
    mistakes = extra_updates.get("mistakes") or details.get("mistakes") or ""
    comments = extra_updates.get("comments") or details.get("comments") or ""
    targets = extra_updates.get("targets") or details.get("targets")
    htf_1h = details.get("htf_1h")
    ltf_3m = details.get("ltf_3m")
    htf_4h = details.get("htf_4h")
    after_url = details.get("after_url")

    # Date parse for Year / Month
    try:
        dt_clean = date_str[:10]
        dt_obj = datetime.strptime(dt_clean, "%Y-%m-%d")
        year_str = str(dt_obj.year)
        month_str = MONTH_NAMES_RU.get(dt_obj.month, "Октябрь")
    except Exception:
        year_str = "2026"
        month_str = "Октябрь"

    new_trade_num = get_next_trade_number(year_str=year_str, month_str=month_str, mode=to_mode)
    adapted_poi = adapt_poi_for_mode(poi, to_mode)
    adapted_targets = adapt_targets_for_mode(targets, to_mode)

    new_trade_res = add_trade(
        trade_num=new_trade_num,
        asset=asset,
        direction=direction,
        session=session,
        rr=rr,
        timeframe=entry,
        model=adapted_poi,
        date_str=date_str,
        htf_1h_url=htf_1h,
        ltf_3m_url=ltf_3m,
        htf_4h_url=htf_4h,
        after_url=after_url,
        target_tags=adapted_targets,
        notes=notes,
        reasoning=notes,
        pnl_dollar=pnl,
        mode=to_mode,
        risk=risk,
        mistakes=mistakes,
        comments=comments
    )

    if new_trade_res.get("success"):
        # Archive old page
        try:
            notion_api_request("PATCH", f"https://api.notion.com/v1/pages/{page_id}", json_payload={"archived": True})
            print(f"🗑️ Старая страница {page_id} ({from_mode}) архивирована при переносе в {to_mode}")
        except Exception as e:
            print(f"⚠️ Ошибка архивирования старой страницы {page_id}: {e}")

        mode_names = {"real": "Реальные сделки", "backtest": "Бэктесты", "missed": "Сделки профукал"}
        return {
            "success": True,
            "page_id": new_trade_res.get("page_id"),
            "url": new_trade_res.get("url"),
            "trade_num": new_trade_num,
            "mode": to_mode,
            "year": year_str,
            "month": month_str,
            "date": date_str,
            "updates": {
                **extra_updates,
                "mode": f"Перенесена в базу «{mode_names.get(to_mode, to_mode)}» (№{new_trade_num})"
            }
        }
    return new_trade_res


def parse_and_update_trade(text: str, last_trade_info: dict = None, mode: str = "real"):
    """
    Parses correction voice or text message and updates the last trade in Notion.
    """
    if not last_trade_info or not last_trade_info.get("page_id"):
        last_trade_info = get_most_recent_trade_page(mode=mode)
    if not last_trade_info or not last_trade_info.get("page_id"):
        return {"success": False, "error": "Не найдена сделка для исправления."}

    page_id = last_trade_info.get("page_id")
    trade_mode = last_trade_info.get("mode", mode)
    trade_num = str(last_trade_info.get("trade_num", "")).rstrip(".")
    page_url = last_trade_info.get("url", f"https://app.notion.com/p/{page_id.replace('-', '')}")

    updates = {}

    # 0. Target mode detection (move to backtest / real / missed)
    target_mode = None
    if re.search(r"\b(?:это\s+)?(?:в|на)\s*бэктест\w*|бэктест\w*|backtest\w*|должн\w*\s*быть\s*в\s*бэктест\w*", text, re.I):
        if not re.search(r"не\s+(?:в\s*)?бэктест", text, re.I):
            target_mode = "backtest"
    if not target_mode and re.search(r"\b(?:это\s+)?(?:в|на)\s*реал\w*|реальн\w*|live|настоящ\w*\b", text, re.I):
        if not re.search(r"не\s+(?:в\s*)?реал", text, re.I):
            target_mode = "real"
    if not target_mode and re.search(r"\b(?:это\s+)?(?:в|на)\s*профукал\w*|пропущен\w*|missed\b", text, re.I):
        target_mode = "missed"

    # 1. Asset detection in correction
    m_not_a = re.search(r"не\s+([а-яa-z0-9&_]+)[\s,]+а\s+([а-яa-z0-9&_]+)", text, re.I)
    if m_not_a:
        candidate = m_not_a.group(2).upper()
        if re.search(r"sp\s*500|s&p\s*500|us\s*500|spx|es|сипи|эспи|эсэндпи", candidate, re.I):
            updates["asset"] = "US500"
        elif "евро" in candidate.lower() or "eur" in candidate.lower():
            updates["asset"] = "EURUSD"
        elif "фунт" in candidate.lower() or "gbp" in candidate.lower():
            updates["asset"] = "GBPUSD"
        elif "золот" in candidate.lower() or "xau" in candidate.lower() or "gold" in candidate.lower():
            updates["asset"] = "XAU"
        elif "герман" in candidate.lower() or "dax" in candidate.lower() or "ger40" in candidate.lower():
            updates["asset"] = "GER40"
        elif "биток" in candidate.lower() or "btc" in candidate.lower():
            updates["asset"] = "BTCUSD"
        elif "франк" in candidate.lower() or "chf" in candidate.lower():
            updates["asset"] = "USDCHF"
        elif "насдак" in candidate.lower() or "us100" in candidate.lower() or "nas100" in candidate.lower():
            updates["asset"] = "US100"
        elif "uk100" in candidate.lower() or "ftse" in candidate.lower():
            updates["asset"] = "UK100" if trade_mode == "real" else "US100"
        elif candidate in ASSET_ALIASES:
            updates["asset"] = ASSET_ALIASES[candidate]
        else:
            updates["asset"] = candidate

    if "asset" not in updates:
        if re.search(r"\b(sp\s*500|s&p\s*500|us\s*500|spx\w*|es|сипи\s*500|сипи|эспи|эсэндпи|эс\s*энд\s*пи|[scср]\s*[pр]\s*500)\b", text, re.I):
            updates["asset"] = "US500"
        elif re.search(r"\b(us\s*100|us100|nasdaq|насдак|nas100|ndx)\b", text, re.I):
            updates["asset"] = "US100"
        elif re.search(r"\b(uk\s*100|uk100|ftse\w*)\b", text, re.I):
            updates["asset"] = "UK100" if trade_mode == "real" else "US100"
        elif re.search(r"\b(фунт|gbp|кабель)\b", text, re.I):
            updates["asset"] = "GBPUSD"
        elif re.search(r"\b(евро|eur|еврик)\b", text, re.I) and not re.search(r"не\s+(?:евро|eur)", text, re.I):
            updates["asset"] = "EURUSD"
        elif re.search(r"\b(франк|chf)\b", text, re.I) and not re.search(r"франк[-\s]*манипуляц", text, re.I):
            updates["asset"] = "USDCHF"
        elif re.search(r"\b(германия|дакс|dax|ger40|ger)\b", text, re.I):
            updates["asset"] = "GER40"
        elif re.search(r"\b(золото|gold|xau)\b", text, re.I):
            updates["asset"] = "XAU"
        elif re.search(r"\b(биток|btc)\b", text, re.I):
            updates["asset"] = "BTCUSD"

    # 2. Date / Month / Year detection
    m_yr = re.search(r"\b(20[2-3]\d)\b", text)
    corr_year = m_yr.group(1) if m_yr else (last_trade_info.get("year") or "2026")

    corr_month = None
    months_regex = [
        (1, r"\bянвар[яеьеию]?\w*\b"),
        (2, r"\bфеврал[яеьеию]?\w*\b"),
        (3, r"\bмарт[аеьеию]?\w*\b"),
        (4, r"\bапрел[яеьеию]?\w*\b"),
        (5, r"\b(?:ма[яе]|мая|май)\b"),
        (6, r"\bиюн[яеьеию]?\w*\b"),
        (7, r"\bиюл[яеьеию]?\w*\b"),
        (8, r"\bавгуст[аеьеию]?\w*\b"),
        (9, r"\bсентябр[яеьеию]?\w*\b"),
        (10, r"\bоктябр[яеьеию]?\w*\b"),
        (11, r"\bноябр[яеьеию]?\w*\b"),
        (12, r"\bдекабр[яеьеию]?\w*\b"),
    ]
    for m_val, m_pattern in months_regex:
        if re.search(m_pattern, text, re.I):
            corr_month = m_val
            break

    corr_day = None
    m_day = re.search(
        r"(?:за\s*|сделка\s*за\s*|число\s*[-—–:]?\s*)?(\d{1,2})\s*(?:-?[еяго]|числа)?\s*(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]|май|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)",
        text,
        re.I
    )
    if m_day:
        corr_day = int(m_day.group(1))
    else:
        m_day2 = re.search(r"(?:число\s*[-—–:]?\s*)(\d{1,2})\b", text, re.I)
        if m_day2:
            corr_day = int(m_day2.group(1))
        else:
            m_day3 = re.search(r"(\d{1,2})\s*(?:-?[еяго]|число|числа)\b", text, re.I)
            if m_day3:
                corr_day = int(m_day3.group(1))

    if corr_month or corr_day:
        now = datetime.now()
        final_month = corr_month if corr_month else (now.month if int(corr_year) == now.year else 10)
        final_day = corr_day if corr_day else (now.day if int(corr_year) == now.year and final_month == now.month else 5)
        m_time = re.search(r"\b([012]?\d):([0-5]\d)\b", text)
        if m_time:
            updates["date"] = f"{corr_year}-{final_month:02d}-{final_day:02d}T{int(m_time.group(1)):02d}:{int(m_time.group(2)):02d}:00"
        else:
            updates["date"] = f"{corr_year}-{final_month:02d}-{final_day:02d}"

    # 3. Direction
    if re.search(r"\b(short|шорт|продаж\w*|вниз|нисходящ\w*|медвеж\w*|сел[л]?|sell)\b", text, re.I):
        updates["direction"] = "Short"
    elif re.search(r"\b(long|лонг|покупк\w*|вверх|восходящ\w*|бычь\w*|бай|buy)\b", text, re.I):
        updates["direction"] = "Long"

    # 4. POI / Model & Session
    has_frank = bool(
        re.search(r"франк|франт|frank|обратн\w*\s*манипуляц", text, re.I)
        and re.search(r"манипуляц|manipu|обратн", text, re.I)
    ) or bool(re.search(r"обратная\s*манипуляция", text, re.I))

    poi_list = []
    if has_frank:
        poi_list.append("Frankfurt 1h3m" if trade_mode == "real" else "frankfurt manipulation")
        if not re.search(r"\b(франкфурт\s*сесси|сессия\s*франк|до\s*10)\b", text, re.I):
            updates["session"] = "London"
        else:
            updates["session"] = "Frankfurt"
    if re.search(r"(?:fvg|fwg|фвг|имбаланс)\s*(?:1h|1ч|часов)|(?:1h|1ч)\s*(?:fvg|fwg|фвг)", text, re.I):
        if "Fvg 1h" not in poi_list:
            poi_list.append("Fvg 1h")
    if re.search(r"(?:fvg|fwg|фвг|имбаланс)\s*(?:4h|4ч)|(?:4h|4ч)\s*(?:fvg|fwg|фвг)", text, re.I):
        poi_list.append("fvg 4h" if trade_mode == "real" else "Fvg 4h")
    if re.search(r"\b(silver\s*bullet|сильвер|пул[яе]|bullet)\b", text, re.I):
        poi_list.append("silver bullet")
    if re.search(r"(?:с[вф]ип|сняти\w*)\s*(?:pdh|pdl|пдх|пдл)", text, re.I) and trade_mode == "real":
        poi_list.append("снятие PDH/PDL")

    if poi_list:
        updates["poi"] = poi_list

    # 5. Entry TF
    if re.search(r"(?:bos|бос)[\s\w]{0,10}(?:1m|1м|m1|м1)|\b(?:1m|1м|m1|м1)\b[\s\w]{0,10}(?:bos|бос)", text, re.I):
        updates["entry"] = "M1"
    elif re.search(r"(?:bos|бос)[\s\w]{0,10}(?:3m|3м|m3|м3)|\b(?:3m|3м|m3|м3)\b[\s\w]{0,10}(?:bos|бос)", text, re.I):
        updates["entry"] = "M3"
    elif re.search(r"(?:bos|бос)[\s\w]{0,10}(?:5m|5м|m5|м5)|\b(?:5m|5м|m5|м5)\b[\s\w]{0,10}(?:bos|бос)", text, re.I):
        updates["entry"] = "M5"

    # 6. Target
    targets = []
    if re.search(r"нью[-\s]*йорк|new[-\s]*york|\bny\b", text, re.I):
        targets.append("high/low NY")
    if re.search(r"pdh|pdl|пдл|пдх", text, re.I):
        targets.append("PDH/PDL" if trade_mode == "real" else "PDL/PDH")
    if re.search(r"pwh|pwl|пвл|пвх", text, re.I):
        targets.append("PWL/PWH")
    if re.search(r"ази[ияю]|asia", text, re.I):
        targets.append("high/low asia")
    if re.search(r"лондон|london", text, re.I) and re.search(r"таргет|цель|до", text, re.I):
        targets.append("high/low London")
    if re.search(r"fvg\s*4h|имбаланс\s*4ч|четырехчасов\w*|4h", text, re.I):
        targets.append("fvg 4h")
    elif re.search(r"fvg\s*1h|имбаланс\s*1ч", text, re.I):
        targets.append("fvg 1h")
    elif re.search(r"fvg\s*30m|30м", text, re.I):
        targets.append("fvg 30m")
    if targets:
        updates["targets"] = targets

    # 7. RR
    if re.search(r"\b(убыточн\w*|убыток|минусов\w*|в\s*минус|стоп|минус\s*1|-1\s*[rRрР]?|loss|sl)\b", text, re.I):
        updates["rr"] = -1.0
    elif re.search(r"\b(безубыток|бу|be)\b", text, re.I):
        updates["rr"] = 0.1
    else:
        m_rr = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:[rRрР]|к\s*1)", text, re.I)
        if m_rr:
            updates["rr"] = normalize_rr(float(m_rr.group(1).replace(",", ".")))

    # 8. Risk %
    m_risk = re.search(r"(?:риск\w*|под|процент\w*)[:\s—–]*([012](?:[.,]\d+)?)\s*(?:%|процент\w*)?", text, re.I)
    if not m_risk:
        m_risk = re.search(r"\b([012](?:[.,]\d+)?)\s*%\s*(?:риск\w*)?", text, re.I)
    if m_risk:
        try:
            r_num = float(m_risk.group(1).replace(",", "."))
            if r_num in (0.5, 1.0, 1.5, 2.0) or (0 < r_num <= 5):
                updates["risk"] = f"{r_num:g}%"
        except Exception:
            pass

    # Calculate effective RR and PNL for updates
    if "rr" in updates or "risk" in updates:
        curr_details = get_trade_page_details(page_id, mode=trade_mode) or {}
        raw_rr = float(updates.get("rr", curr_details.get("rr", 0.0)))
        risk_input = updates.get("risk") or curr_details.get("risk") or "1%"
        eff_rr, calc_pnl, r_f = compute_effective_rr_and_pnl(raw_rr, risk_input, pnl_dollar=updates.get("pnl_dollar"))
        updates["rr"] = eff_rr
        updates["raw_rr"] = raw_rr
        updates["pnl_dollar"] = calc_pnl

    # 9. Mistakes & Pometki
    pometki = []
    if re.search(r"неправильн\w*\s*(?:таргет|цел\w*)|таргет\s*неправильн\w*|не\s*тот\s*таргет", text, re.I):
        pometki.append("Неправильный таргет")
    if re.search(r"не\s*по\s*тренду|против\s*тренда", text, re.I):
        pometki.append("Не по тренду")
    if re.search(r"неправильн\w*\s*(?:sl|стоп)", text, re.I):
        pometki.append("Неправильный SL")
    if re.search(r"эмоци|азарт|тревог|страх", text, re.I):
        pometki.append("Эмоции")
    if pometki:
        updates["pometki"] = pometki

    m_mistake = re.search(r"(?:моя\s*)?ошибк\w*[:\s—–]*(.+?)(?=(?:комментари\w*|коммент\w*|выдержка\w*|мысли\w*|\n|$))", text, re.I)
    if m_mistake:
        mistake_clean = m_mistake.group(1).strip().rstrip(".,;")
        if "Неправильный таргет" in pometki and "неправильный таргет" not in mistake_clean.lower():
            mistake_clean = f"Неправильный таргет: {mistake_clean}"
        updates["mistakes"] = re.sub(r"https?://\S+", "", mistake_clean).strip().rstrip(".,;")
        updates["improve_tags"] = ["Анализ" if "Неправильный таргет" in pometki else "Исполнение"]
    elif "Неправильный таргет" in pometki:
        updates["mistakes"] = "Неправильный таргет"
        updates["improve_tags"] = ["Анализ"]

    m_comment = re.search(r"(?:комментари\w*|коммент\w*|выдержка\w*|мысли\w*)[:\s—–]*(.+?)(?=(?:ошибк\w*|\n|$))", text, re.I)
    if m_comment:
        updates["comments"] = re.sub(r"https?://\S+", "", m_comment.group(1)).strip().rstrip(".,;")

    # 10. Notes
    has_explicit_notes = bool(re.search(r"тезис|причина|описани|заметка|пометк", text, re.I))

    notes_parts = []
    if "poi" in updates:
        poi_str = ", ".join(updates["poi"]) if isinstance(updates["poi"], list) else str(updates["poi"])
        notes_parts.append(poi_str)
    if "entry" in updates:
        notes_parts.append(f"BoS {updates['entry']}")
    if "targets" in updates:
        t_str = ", ".join(updates["targets"]) if isinstance(updates["targets"], list) else str(updates["targets"])
        notes_parts.append(f"Таргет: {t_str}")

    if has_explicit_notes and notes_parts:
        updates["notes"] = ". ".join(notes_parts) + "."

    if target_mode and target_mode != trade_mode:
        return move_trade_to_mode(page_id, from_mode=trade_mode, to_mode=target_mode, extra_updates=updates)

    if target_mode and target_mode == trade_mode and not updates:
        mode_names = {"real": "Реальные сделки", "backtest": "Бэктесты", "missed": "Сделки профукал"}
        return {
            "success": True,
            "page_id": page_id,
            "url": page_url,
            "trade_num": trade_num,
            "mode": trade_mode,
            "updates": {"mode": f"Сделка уже находится в базе «{mode_names.get(trade_mode, trade_mode)}»"}
        }

    res = update_trade_properties(page_id, updates, mode=trade_mode)
    if res.get("success"):
        return {
            "success": True,
            "page_id": page_id,
            "url": page_url,
            "trade_num": trade_num,
            "mode": trade_mode,
            "updates": updates
        }
    else:
        return res


def parse_trade_draft(text: str, default_year="2026", default_month="Январь", mode="backtest", manual_balance=None):
    """
    Parses a free-form message with trading details, voice transcriptions,
    and TradingView links, and constructs a structured draft dictionary
    without writing anything to Notion yet.
    Allows user review, risk cycling, direction toggle, and database selection.
    """
    if re.search(r"профукал|пропустил|проспал|не успел|не заметил|забыл открыть|упущен", text, re.I):
        mode = "missed"
    elif re.search(r"\b(в\s*реале|на\s*реале|реальн(?:ая|ые|ый|ую|ом)\s*сделк\w*|live|на\s*настоящ\w*|сделка\s*в\s*реале|потерял.*доллар|прибыль.*доллар|заработал.*доллар)\b", text, re.I):
        mode = "real"
    elif re.search(r"\b(бэктест\w*|backtest\w*|на\s*бэктесте|в\s*бэктесте)\b", text, re.I):
        mode = "backtest"

    # 1. Extract links (TradingView snapshots)
    tv_links = re.findall(r"https?://(?:www\.)?tradingview\.com/x/[A-Za-z0-9]+/?", text)
    htf_1h = None
    ltf_3m = None
    htf_4h = None
    after_url = None

    m_after = re.search(r"(?:after|после|итог)[\s:=]+(https?://\S+)", text, re.I)
    if m_after:
        after_url = m_after.group(1).rstrip(",.;")
    m_4h = re.search(r"(?:4h|4ч)[\s:=]+(https?://\S+)", text, re.I)
    if m_4h:
        htf_4h = m_4h.group(1).rstrip(",.;")
    m_1h = re.search(r"(?:1h|1ч|htf)[\s:=]+(https?://\S+)", text, re.I)
    if m_1h:
        htf_1h = m_1h.group(1).rstrip(",.;")
    m_3m = re.search(r"(?:3m|3м|ltf|1m|1м)[\s:=]+(https?://\S+)", text, re.I)
    if m_3m:
        ltf_3m = m_3m.group(1).rstrip(",.;")

    remaining_links = [l for l in tv_links if l not in (htf_1h, ltf_3m, htf_4h, after_url)]
    if not htf_1h and remaining_links:
        htf_1h = remaining_links.pop(0)
    if not ltf_3m and remaining_links:
        ltf_3m = remaining_links.pop(0)
    if not htf_4h and remaining_links:
        htf_4h = remaining_links.pop(0)
    if not after_url and remaining_links:
        after_url = remaining_links.pop(0)

    # 2. Asset detection
    asset = None
    if mode == "missed":
        active_lookups = LOOKUPS_MISSED
    elif mode == "real":
        active_lookups = LOOKUPS_REAL
    else:
        active_lookups = LOOKUPS_BACKTEST
    assets_list = list(active_lookups.get("Assets", {}).get("items", {}).keys())

    for a in assets_list:
        if re.search(rf"\b{a}\b", text, re.I):
            asset = a
            break

    if not asset:
        if re.search(r"\b(sp\s*500|s&p\s*500|us\s*500|spx\w*|es|сипи\s*500|сипи|эспи|эсэндпи|эс\s*энд\s*пи|[scср]\s*[pр]\s*500)\b", text, re.I):
            asset = "US500"
        elif re.search(r"\b(uk\s*100|uk100|ftse\w*)\b", text, re.I):
            asset = "UK100" if (mode == "real" and "UK100" in assets_list) else "US100"
        elif re.search(r"\b(us\s*100|us100|nasdaq|насдак|nas100|ndx)\b", text, re.I):
            asset = "US100"
        elif re.search(r"\b(фунт|gbp|кабель)\b", text, re.I):
            asset = "GBPUSD"
        elif re.search(r"\b(евро|eur|еврик)\b", text, re.I):
            asset = "EURUSD"
        elif re.search(r"\b(франк|chf)\b", text, re.I):
            asset = "USDCHF"
        elif re.search(r"\b(германия|дакс|dax|ger40|ger|gear|гер|гэр)\b", text, re.I):
            asset = "GER40"
        elif re.search(r"\b(золото|gold|xau)\b", text, re.I):
            asset = "XAU"
        elif re.search(r"\b(биток|btc)\b", text, re.I):
            asset = "BTCUSD"

    # If still not found in text, extract ticker from TradingView links!
    if not asset and tv_links:
        for link in tv_links:
            tv_sym = get_tv_asset(link)
            if tv_sym:
                if tv_sym in assets_list:
                    asset = tv_sym
                    break
                elif tv_sym in ASSET_ALIASES and ASSET_ALIASES[tv_sym] in assets_list:
                    asset = ASSET_ALIASES[tv_sym]
                    break
                elif tv_sym == "UK100":
                    asset = "UK100" if (mode == "real" and "UK100" in assets_list) else "US100"
                    break

    if not asset:
        asset = "US500" if ("500" in text or "sp" in text.lower()) else "EURUSD"

    # 3. Direction, Transaction Type & Trend detection
    direction = "Long"
    has_long = bool(re.search(r"\b(long|лонг|покупк\w*|в\s*лонг|бай|buy)\b", text, re.I))
    has_short = bool(re.search(r"\b(short|шорт|продаж\w*|в\s*шорт|сел[л]?|sell)\b", text, re.I))
    if has_long and not has_short:
        direction = "Long"
    elif has_short and not has_long:
        direction = "Short"
    elif has_long and has_short:
        if re.search(r"(?:зашел|был|пошел|вход\w*|открыл\w*|брал)\s*(?:в\s*)?лонг", text, re.I):
            direction = "Long"
        elif re.search(r"(?:зашел|был|пошел|вход\w*|открыл\w*|брал)\s*(?:в\s*)?шорт", text, re.I):
            direction = "Short"
        else:
            direction = "Long"
    elif re.search(r"до\s*(?:пдл|pdl|лоя)", text, re.I):
        direction = "Short"
    elif re.search(r"до\s*(?:пдх|pdh|хая)", text, re.I):
        direction = "Long"

    transaction_type = "Continuation"
    if re.search(r"\b(reversal|разворот\w*|контртренд\w*|контр-тренд\w*)\b", text, re.I):
        transaction_type = "Reversal"
    elif re.search(r"против\s*тренда|не\s*по\s*тренду", text, re.I):
        transaction_type = "Reversal"
    elif re.search(r"\b(range|рендж|боковик\w*|флэт\w*|девиаци\w*)\b", text, re.I):
        transaction_type = "Range"
    elif re.search(r"\b(continuation|продолжени\w*|по\s*тренду)\b", text, re.I):
        transaction_type = "Continuation"

    trend = None
    if re.search(r"\b(downtrend|даунтренд\w*|даунтрэнд\w*|нисходящ\w*\s*тренд|тренд\s*вниз)\b", text, re.I):
        trend = "downtrend"
    elif re.search(r"\b(uptrend|аптренд\w*|аптрэнд\w*|восходящ\w*\s*тренд|тренд\s*вверх)\b", text, re.I):
        trend = "uptrend"
    elif transaction_type == "Reversal" or re.search(r"против\s*тренда|не\s*по\s*тренду", text, re.I):
        trend = "downtrend" if direction == "Long" else "uptrend"
    else:
        trend = "uptrend" if direction == "Long" else "downtrend"

    # 4. Dollar Profit (PNL) & RR detection
    pnl_dollar = None
    m_pnl = re.search(r"(?:прибыль|профит|плюс|заработал|вышло|итог|pnl)\s*[:=]?\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:доллар\w*|бакс\w*|\$|usd)", text, re.I)
    if not m_pnl:
        m_pnl = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:доллар\w*|бакс\w*|\$|usd)", text, re.I)
    if not m_pnl:
        m_pnl = re.search(r"(?:\$|usd)\s*([+-]?\d+(?:[.,]\d+)?)", text, re.I)
    if m_pnl:
        try:
            pnl_dollar = float(m_pnl.group(1).replace(",", "."))
            if re.search(r"убыток|минус|loss|потерял", text, re.I) and pnl_dollar > 0:
                pnl_dollar = -pnl_dollar
        except Exception:
            pnl_dollar = None

    if pnl_dollar is None:
        russian_num_words = {
            "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
            "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
            "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
            "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
            "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
            "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
            "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
            "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19
        }
        m_words = re.search(r"(?:потерял|убыток|минус|прибыль|профит|плюс|заработал)\s*(?:я\s*)?([а-я\s\d]+?)\s*(?:доллар\w*|\$|usd)(?:\s*(\d{1,2}))?", text, re.I)
        if m_words:
            val_part = m_words.group(1).strip()
            cents_part = m_words.group(2)
            total = 0.0
            found_any = False
            for w in val_part.split():
                if w in russian_num_words:
                    total += russian_num_words[w]
                    found_any = True
                elif w.isdigit():
                    total += int(w)
                    found_any = True
            if found_any:
                is_correction = bool(re.search(r"\b" + re.escape(cents_part or "xyz") + r"\s*(?:точнее|вернее|точней)", text, re.I))
                if cents_part and not is_correction:
                    total += int(cents_part) / 100.0
                if re.search(r"потерял|убыток|минус|loss", text, re.I):
                    total = -total
                pnl_dollar = total

    # Check for corrections like "85 точнее", "точнее 85", "не -184, а -85"
    m_correction = re.search(r"не\s*[+-]?\d+(?:[.,]\d+)?\s*(?:доллар\w*|\$|usd)?\s*,\s*а\s*([+-]?\d+(?:[.,]\d+)?)", text, re.I)
    if not m_correction:
        m_correction = re.search(r"(?:доллар\w*|\$|usd)[\s,]+(\d+(?:[.,]\d+)?)\s*(?:точнее|вернее|точней)", text, re.I)
    if not m_correction:
        m_correction = re.search(r"(?:точнее|вернее|точней)\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:доллар\w*|\$|usd)?", text, re.I)
    if not m_correction:
        m_correction = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:точнее|вернее|точней)", text, re.I)

    if m_correction:
        val = float(m_correction.group(1).replace(",", "."))
        if re.search(r"убыток|минус|loss|потерял", text, re.I) or "-" in m_correction.group(0):
            pnl_dollar = -abs(val)
        else:
            pnl_dollar = abs(val)

    is_loss = bool(re.search(r"\b(убыточн\w*|убыток|минусов\w*|в\s*минус|стоп|минус\s*1|-1\s*[rRрР]?|loss|sl)\b", text, re.I))
    m_spaced = re.search(r"(?:рр|rr|эр|тейк)?\s*(\d{1,2})\s+(\d{2})\b", text, re.I)
    m_rr3 = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:rr|рр|эр)", text, re.I)
    m_rr2 = re.search(r"(?:rr|рр|эр|тейк)\s*[:=]?\s*([+-]?\d+(?:[.,]\d+)?)", text, re.I)
    m_rr = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:[rRрР]|к\s*1)", text, re.I)

    if is_loss:
        rr = -1.0
    elif m_spaced and ("рр" in text.lower() or "rr" in text.lower() or "эр" in text.lower() or "получилось" in text.lower()):
        rr = float(f"{m_spaced.group(1)}.{m_spaced.group(2)}")
    elif m_rr3:
        rr = normalize_rr(float(m_rr3.group(1).replace(",", ".")))
    elif m_rr2:
        rr = normalize_rr(float(m_rr2.group(1).replace(",", ".")))
    elif m_rr:
        rr = normalize_rr(float(m_rr.group(1).replace(",", ".")))
    else:
        m_poluch = re.search(r"получилось\s*(?:у\s*меня\s*)?([+-]?\d+(?:[.,]\d+)?)", text, re.I)
        if m_poluch:
            rr = normalize_rr(float(m_poluch.group(1).replace(",", ".")))
        elif re.search(r"\b(безубыток|бу|be)\b", text, re.I):
            rr = 0.1
        elif re.search(r"\b(тейк|плюс|tp|take|отработала|дошла|реализовалась)\b", text, re.I):
            rr = 2.2 if mode in ("missed", "real") else 2.0
        else:
            rr = 2.0

    # 5. Entry Timeframe (Based on BoS!)
    entry_tf = "M3"
    if re.search(r"(?:bos+|бос+)[\s\w]{0,15}\b(?:15m|15м|m15|м15)\b|\b(?:15m|15м|m15|м15)\b[\s\w]{0,15}(?:bos+|бос+)", text, re.I):
        entry_tf = "M15"
    elif re.search(r"(?:bos+|бос+)[\s\w]{0,15}\b(?:5m|5м|m5|м5)\b|\b(?:5m|5м|m5|м5)\b[\s\w]{0,15}(?:bos+|бос+)", text, re.I):
        entry_tf = "M5"
    elif re.search(r"(?:bos+|бос+)[\s\w]{0,15}\b(?:1m|1м|m1|м1|минутк\w*)\b|\b(?:1m|1м|m1|м1|минутк\w*)\b[\s\w]{0,15}(?:bos+|бос+)", text, re.I):
        entry_tf = "M1"
    elif re.search(r"(?:bos+|бос+)[\s\w]{0,15}\b(?:3m|3м|m3|м3)\b|\b(?:3m|3м|m3|м3)\b[\s\w]{0,15}(?:bos+|бос+)", text, re.I):
        entry_tf = "M3"

    # 6. POI detection
    has_frank = bool(
        re.search(r"франк|франт|frank|обратн\w*\s*манипуляц", text, re.I) 
        and re.search(r"манип|man[iu]p|обратн", text, re.I)
    ) or bool(re.search(r"обратная\s*манипуляция", text, re.I))
    has_fvg_1h = bool(re.search(
        r"(?:fvg|fwg|фвг|имбаланс)\s*(?:1h|1\s*h|1ч|1\s*ч|одног?о?часов\w*|часов\w*)|(?:1h|1\s*h|1ч|1\s*ч|одног?о?часов\w*|часов\w*)\s*(?:fvg|fwg|фвг|имбаланс)",
        text, re.I
    ))
    has_fvg_4h = bool(re.search(
        r"(?:fvg|fwg|фвг|имбаланс)\s*(?:4h|4\s*h|4ч|4\s*ч|четырехчасов\w*)|(?:4h|4\s*h|4ч|4\s*ч|четырехчасов\w*)\s*(?:fvg|fwg|фвг|имбаланс)",
        text, re.I
    ))
    has_fvg_15m = bool(re.search(
        r"(?:fvg|fwg|фвг|имбаланс)\s*(?:15m|15\s*m|15м|15\s*м|пятнадцатиминут\w*)|(?:15m|15\s*m|15м|15\s*м|пятнадцатиминут\w*)\s*(?:fvg|fwg|фвг|имбаланс)",
        text, re.I
    ))
    has_fvg_30m = bool(re.search(
        r"(?:fvg|fwg|фвг|имбаланс)\s*(?:30m|30\s*m|30м|30\s*м|тридцатиминут\w*)|(?:30m|30\s*m|30м|30\s*м|тридцатиминут\w*)\s*(?:fvg|fwg|фвг|имбаланс)",
        text, re.I
    ))
    has_generic_fvg = bool(re.search(r"\b(?:fvg|fwg|фвг|имбаланс)\b", text, re.I))
    has_silver = bool(re.search(r"\b(silver\s*bullet|сильвер|пул[яе]|bullet)\b", text, re.I))
    has_pdh_sweep = bool(re.search(r"(?:с[вф]ип|сняти\w*)\s*(?:pdh|pdl|пдх|пдл|дневн\w*\s*(?:хай|лой))", text, re.I))
    has_sweep = bool(re.search(r"с[вф]ип|офип|sweep|сняти", text, re.I))
    has_bos = bool(re.search(r"бос[с]?|bos", text, re.I))

    is_asia_target = bool(re.search(r"(?:до|таргет|цель|target|to|фрактал|fractal)[\s\w]{0,15}(?:ази[яи]|asia)", text, re.I))
    is_ny_target = bool(re.search(r"(?:до|таргет|цель|target|to|фрактал|fractal)[\s\w]{0,15}(?:нью[-\s]*йорк|ny|new[-\s]*york)", text, re.I))
    is_london_target = bool(re.search(r"(?:до|таргет|цель|target|to|фрактал|fractal)[\s\w]{0,15}(?:лондон|london)", text, re.I))

    session = None
    if re.search(r"\b(лондон|london)\b", text, re.I) and not is_london_target:
        session = "London"
    elif (re.search(r"\b(нью[-\s]*йорк|new[-\s]*york)\b", text, re.I) or re.search(r"\bny\b", text, re.I)) and not is_ny_target:
        session = "NY"
    elif re.search(r"\b(ази[яи]|asia)\b", text, re.I) and not is_asia_target:
        session = "Asia"
    elif re.search(r"\b(франкфурт|frankfurt|сессия\s*франк|до\s*10|09:[0-5]\d)\b", text, re.I) or has_frank:
        session = "Frankfurt"
    else:
        session = "London"

    models = []
    if has_frank:
        models.append("Frankfurt 1h3m" if mode == "real" else "frankfurt manipulation")
    if has_fvg_1h or (has_generic_fvg and re.search(r"1h|1ч|часов", text, re.I)):
        models.append("Fvg 1h")
    if has_fvg_4h:
        models.append("fvg 4h" if mode == "real" else "Fvg 4h")
    if has_fvg_15m:
        models.append("fvg 15m")
    if has_fvg_30m:
        models.append("fvg 30m" if mode == "real" else "fvg30m")
    if has_silver:
        models.append("silver bullet")
    if has_pdh_sweep and mode == "real":
        models.append("снятие PDH/PDL")
    if has_sweep and has_bos:
        models.append("Sweep+Bos/fvg" if mode == "real" else "Sweep+Bos")
    elif has_sweep:
        models.append("Sweep+Bos/fvg" if mode == "real" else "Sweep+Bos")

    # Deduplicate while preserving order
    seen_m = set()
    unique_models = []
    for m_it in models:
        if m_it not in seen_m:
            seen_m.add(m_it)
            unique_models.append(m_it)

    if not unique_models:
        if re.search(r"1h3m|1ч3м", text, re.I):
            unique_models = ["1h3m"]
        else:
            unique_models = ["Frankfurt 1h3m" if mode == "real" else "Sweep+Bos"]

    # 7. Target tags
    targets = []
    m_target = re.search(r"(?:таргет|цель|target|тянул\s*до|выход\s*по)\s+([^.,;\n]+)", text, re.I)
    target_part = m_target.group(1).lower() if m_target else ""
    target_scope = f"{target_part} {text.lower()}"

    pdl_pdh_tag = "PDH/PDL" if mode == "real" else "PDL/PDH"
    if re.search(r"pdh|pdl|пдл|пдх", target_scope):
        targets.append(pdl_pdh_tag)
    if re.search(r"pwh|pwl|пвл|пвх", target_scope):
        targets.append("PWL/PWH")
    if re.search(r"ази[ияю]|asia", target_scope) or is_asia_target:
        targets.append("high/low asia")
    if (re.search(r"(?:хай|лой|пул|ликвидност\w*|до|target)\s*лондон|лондон\w*\s*(?:хай|лой|пул)", target_scope) or is_london_target) and not re.search(r"\b(сессия\s*лондон|на\s*лондоне|в\s*лондон)\b", target_scope):
        targets.append("high/low London")
    if re.search(r"нью[-\s]*йорк|new[-\s]*york|\bny\b", target_scope) or is_ny_target:
        targets.append("high/low NY")
    if (re.search(r"(?:таргет|цель|target|до)\s+.*?(?:франк|frank)", text, re.I)) and not has_frank:
        targets.append("high/low London" if mode == "backtest" else "Liq 1h")
    if re.search(r"fvg\s*4h|имбаланс\s*4ч|четырехчасов\w*|4h", target_scope):
        targets.append("fvg 4h")
    elif re.search(r"fvg\s*1h|имбаланс\s*1ч", target_scope):
        targets.append("fvg 1h")

    # 8. Date and Month Extraction
    year_num = default_year
    month_num = 1
    day_num = None

    m_yr = re.search(r"\b(20[2-3]\d)\b", text)
    if not m_yr:
        m_yr = re.search(r"\b(?:20)?(2[3-6])\s*(?:год[ае]?|г\.?)\b", text, re.I)
        if m_yr:
            year_num = f"20{m_yr.group(1)}"
    else:
        year_num = m_yr.group(1)

    months_regex = [
        (1, r"\bянвар[яеьеию]?\w*\b"),
        (2, r"\bфеврал[яеьеию]?\w*\b"),
        (3, r"\bмарт[аеьеию]?\w*\b"),
        (4, r"\bапрел[яеьеию]?\w*\b"),
        (5, r"\b(?:ма[яе]|мая|май)\b"),
        (6, r"\bиюн[яеьеию]?\w*\b"),
        (7, r"\bиюл[яеьеию]?\w*\b"),
        (8, r"\bавгуст[аеьеию]?\w*\b"),
        (9, r"\bсентябр[яеьеию]?\w*\b"),
        (10, r"\bоктябр[яеьеию]?\w*\b"),
        (11, r"\bноябр[яеьеию]?\w*\b"),
        (12, r"\bдекабр[яеьеию]?\w*\b"),
    ]
    found_month = False
    for m_val, m_pattern in months_regex:
        if re.search(m_pattern, text, re.I):
            month_num = m_val
            found_month = True
            break
    if not found_month and default_month:
        for m_val, m_pattern in months_regex:
            if re.search(m_pattern, default_month, re.I):
                month_num = m_val
                break

    m_day = re.search(
        r"(?:за\s*|сделка\s*за\s*|число\s*[-—–:]?\s*)?(\d{1,2})\s*(?:-?[еяго]|числа)?\s*(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]|май|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)",
        text,
        re.I
    )
    if m_day:
        day_num = int(m_day.group(1))
    else:
        m_day2 = re.search(r"(?:число\s*[-—–:]?\s*)(\d{1,2})\b", text, re.I)
        if m_day2:
            day_num = int(m_day2.group(1))
        else:
            m_day3 = re.search(r"(\d{1,2})\s*(?:-?[еяго]|число|числа)\b", text, re.I)
            if m_day3:
                day_num = int(m_day3.group(1))
            else:
                m_iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
                if m_iso:
                    year_num = m_iso.group(1)
                    month_num = int(m_iso.group(2))
                    day_num = int(m_iso.group(3))

    if not day_num:
        days_words = {
            "перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6,
            "седьм": 7, "восьм": 8, "девят": 9, "десят": 10, "одиннадцат": 11,
            "двенадцат": 12, "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15,
            "шестнадцат": 16, "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19,
            "двадцат": 20, "тридцат": 30
        }
        for dw_prefix, dw_num in days_words.items():
            if re.search(rf"\b{dw_prefix}\w*\s*(?:уже\s*)?(?:числа\s*)?(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]|май|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)", text, re.I):
                day_num = dw_num
                break

    if not day_num:
        now = datetime.now()
        if int(year_num) == now.year and month_num == now.month:
            day_num = now.day
        else:
            day_num = 15

    # Entry time detection: from explicit text or session defaults
    entry_time = None
    m_time = re.search(r"\b([012]?\d):([0-5]\d)\b", text)
    if m_time:
        h_val, min_val = int(m_time.group(1)), int(m_time.group(2))
        if 0 <= h_val <= 23 and 0 <= min_val <= 59:
            entry_time = f"{h_val:02d}:{min_val:02d}:00"
    if not entry_time:
        if session == "Frankfurt":
            entry_time = "09:15:00"
        elif session == "London":
            entry_time = "10:03:00" if (entry_tf == "M3" or "3m" in text.lower()) else "10:00:00"
        elif session == "NY":
            entry_time = "15:45:00"
        elif session == "Asia":
            entry_time = "03:30:00"
        else:
            entry_time = "10:00:00"

    date_str = f"{year_num}-{month_num:02d}-{day_num:02d} {entry_time}"
    month_name = MONTH_NAMES_RU.get(month_num, "Январь")

    # 9. Clean thesis notes without conversational fluff and WITHOUT URLs
    thesis_parts = []
    is_05 = bool(re.search(r"0[.,]?5|05|половин", text, re.I))

    if has_fvg_1h or (has_generic_fvg and re.search(r"1h|1ч|часов", text, re.I)):
        if is_05 or "сняти" in text.lower():
            thesis_parts.append("Снятие 0.5 FVG 1H")
        elif "дотронулся" in text.lower() or "касани" in text.lower():
            thesis_parts.append("Тест 1H FVG (касание границы)")
        else:
            thesis_parts.append("Тест FVG 1H")
    elif has_fvg_4h:
        thesis_parts.append("Тест 0.5 FVG 4H" if is_05 else "Тест FVG 4H")
    elif has_fvg_30m:
        thesis_parts.append("Снятие 0.5 FVG 30m" if is_05 else "Тест FVG 30m")
    elif has_fvg_15m:
        thesis_parts.append("Тест FVG 15m")
    elif has_generic_fvg:
        thesis_parts.append("Тест 0.5 FVG" if is_05 else "Тест FVG")
    elif has_frank:
        thesis_parts.append("Франкфурт манипуляция")
    elif has_silver:
        thesis_parts.append("Сетап Silver Bullet")
    elif has_sweep:
        if re.search(r"2\s*фрактал|двух\s*фрактал|два\s*фрактал", text, re.I):
            thesis_parts.append("Свип 2-х фракталов")
        elif re.search(r"pdh|pdl|пдх|пдл", text, re.I):
            thesis_parts.append("Свип PDH/PDL")
        elif re.search(r"ази[ияю]|asia", text, re.I):
            if re.search(r"low|лой|лоу", text, re.I):
                thesis_parts.append("Снятие low Азии")
            elif re.search(r"high|хай", text, re.I):
                thesis_parts.append("Снятие high Азии")
            else:
                thesis_parts.append("Снятие ликвидности Азии")
        else:
            thesis_parts.append("Свип ликвидности")

    # Entry BoS confirmation
    thesis_parts.append(f"подтверждение BoS {entry_tf}")

    # Target
    if re.search(r"pdl|пдл", text, re.I):
        thesis_parts.append("таргет PDL")
    elif re.search(r"pdh|пдх", text, re.I):
        thesis_parts.append("таргет PDH")
    elif re.search(r"fvg\s*4h|четырехчасов|4h", text, re.I):
        thesis_parts.append("таргет 4H FVG")
    elif "ази" in text.lower():
        if "лой" in text.lower():
            thesis_parts.append("таргет до лоя Азии")
        elif "хай" in text.lower():
            thesis_parts.append("таргет до хая Азии")
        else:
            thesis_parts.append("таргет ликвидность Азии")
    elif "франк" in text.lower() and ("хай" in text.lower() or "таргет" in text.lower()):
        thesis_parts.append("таргет до хая Франкфурта")
    elif targets:
        thesis_parts.append(f"таргет {', '.join(targets)}")

    if mode == "missed":
        thesis_parts.append("сделка пропущена (профукал)")

    clean_notes = ", ".join(thesis_parts)
    if clean_notes:
        clean_notes = clean_notes[0].upper() + clean_notes[1:] + "."
    else:
        clean_notes = f"Сетап {unique_models[0]}."
    clean_notes = re.sub(r"https?://\S+", "", clean_notes).strip().rstrip(".,;")
    if clean_notes and not clean_notes.endswith("."):
        clean_notes += "."

    # 10. Extract pometki & What to improve
    pometki = []
    improve_tags = []
    anxiety = False

    if mode == "missed":
        improve_tags.append("Скип")
    if re.search(r"неправильн\w*\s*(?:таргет|цел\w*)|таргет\s*неправильн\w*|не\s*тот\s*таргет|таргет.*не\s*до", text, re.I):
        pometki.append("Неправильный таргет")
        improve_tags.append("Анализ")
    if re.search(r"не\s*по\s*тренду|против\s*тренда", text, re.I):
        pometki.append("Не по тренду")
        improve_tags.append("Анализ")
    if re.search(r"неправильн\w*\s*(?:sl|стоп)", text, re.I):
        pometki.append("Неправильный SL")
        improve_tags.append("Исполнение")
    if re.search(r"новост", text, re.I):
        pometki.append("Торговля во время новостей")
        improve_tags.append("Исполнение")
    if re.search(r"эмоци|азарт|тревог|страх|тильт|жертв", text, re.I):
        pometki.append("Эмоции")
        improve_tags.append("Исполнение")
        anxiety = True

    # 11. Dynamic Capital & Risk %
    balance = get_account_balance(mode=mode, manual_balance=manual_balance)
    rec_label, rec_float, rec_bal, rec_status = get_recommended_risk(balance=balance, mode=mode, manual_balance=manual_balance)
    risk_percent = rec_label

    m_risk = re.search(r"(?:риск\w*|под|процент\w*|risk)[:\s—–]*([012](?:[.,]\d+)?)\s*(?:%|процент\w*)?", text, re.I)
    if not m_risk:
        m_risk = re.search(r"\b([012](?:[.,]\d+)?)\s*%\s*(?:риск\w*|risk)?", text, re.I)
    if m_risk:
        try:
            r_num_parsed = float(m_risk.group(1).replace(",", "."))
            if 0 < r_num_parsed <= 5:
                risk_percent = f"{r_num_parsed:g}%"
        except Exception:
            pass

    r_num = parse_risk_val(risk_percent)
    risk_cash = round(balance * (r_num / 100.0), 2)
    effective_rr, final_pnl, _ = compute_effective_rr_and_pnl(rr, r_num, pnl_dollar=pnl_dollar, deposit=balance)

    # 12. Mistakes & Comments
    mistakes_text = ""
    m_mistake = re.search(r"(?:моя\s*)?ошибк\w*[:\s—–]*(.+?)(?=(?:комментари\w*|коммент\w*|выдержка\w*|мысли\w*|\n|$))", text, re.I)
    if m_mistake:
        mistake_clean = m_mistake.group(1).strip().lstrip(",;: ").rstrip(".,;")
        if "Неправильный таргет" in pometki and "неправильный таргет" not in mistake_clean.lower():
            mistake_clean = f"Неправильный таргет ({mistake_clean})"
        elif "Не по тренду" in pometki and "не по тренду" not in mistake_clean.lower():
            mistake_clean = f"Не по тренду ({mistake_clean})"
        mistakes_text = re.sub(r"https?://\S+", "", mistake_clean).strip().rstrip(".,;")
        if "Исполнение" not in improve_tags and "Анализ" not in improve_tags:
            improve_tags.append("Исполнение")
    elif "Неправильный таргет" in pometki:
        mistakes_text = "Неправильный таргет"

    comments_text = ""
    m_comment = re.search(r"(?:комментари\w*|коммент\w*|выдержка\w*|мысли\w*)[:\s—–]*(.+?)(?=(?:ошибк\w*|\n|$))", text, re.I)
    if m_comment:
        comments_text = re.sub(r"https?://\S+", "", m_comment.group(1)).strip().rstrip(".,;")

    return {
        "asset": asset,
        "direction": direction,
        "trend": trend,
        "session": session,
        "transaction_type": transaction_type,
        "raw_rr": float(rr),
        "rr": effective_rr,
        "pnl_dollar": final_pnl,
        "timeframe": entry_tf,
        "model": unique_models,
        "date_str": date_str,
        "year": str(year_num),
        "month": month_name,
        "day": day_num,
        "entry_time": entry_time,
        "htf_1h_url": htf_1h,
        "ltf_3m_url": ltf_3m,
        "htf_4h_url": htf_4h,
        "after_url": after_url,
        "target_tags": targets if targets else None,
        "pometki": pometki if pometki else None,
        "improve_tags": improve_tags if improve_tags else None,
        "anxiety": anxiety,
        "notes": clean_notes,
        "mode": mode,
        "risk": risk_percent,
        "risk_float": r_num,
        "risk_cash": risk_cash,
        "balance": balance,
        "risk_status": rec_status,
        "mistakes": mistakes_text,
        "comments": comments_text
    }


def add_trade_from_draft(draft: dict, target_mode: str = None):
    """
    Saves an approved draft card into the target Notion database.
    """
    mode = target_mode or draft.get("mode", "backtest")
    raw_models = draft.get("model")
    adapted_models = adapt_poi_for_mode(raw_models, mode)
    raw_targets = draft.get("target_tags")
    adapted_targets = adapt_targets_for_mode(raw_targets, mode)

    year_str = str(draft.get("year", "2026"))
    month_str = str(draft.get("month", "Январь"))
    trade_num = f"{get_next_trade_number(year_str=year_str, month_str=month_str, mode=mode)}."

    entry_zone = ["FVG 1h"] if ("Fvg 1h" in adapted_models and mode == "real") else None

    return add_trade(
        trade_num=trade_num,
        asset=draft.get("asset", "US500"),
        direction=draft.get("direction", "Long"),
        trend=draft.get("trend"),
        session=draft.get("session", "London"),
        transaction_type=draft.get("transaction_type", "Continuation"),
        rr=draft.get("raw_rr", draft.get("rr", 2.0)),
        timeframe=draft.get("timeframe", "M3"),
        model=adapted_models,
        date_str=draft.get("date_str"),
        htf_1h_url=draft.get("htf_1h_url"),
        ltf_3m_url=draft.get("ltf_3m_url"),
        htf_4h_url=draft.get("htf_4h_url"),
        after_url=draft.get("after_url"),
        target_tags=adapted_targets,
        entry_zone_tags=entry_zone,
        pometki=draft.get("pometki"),
        improve_tags=draft.get("improve_tags"),
        anxiety=draft.get("anxiety", False),
        notes=draft.get("notes", ""),
        reasoning=draft.get("notes", ""),
        pnl_dollar=draft.get("pnl_dollar"),
        mode=mode,
        risk=draft.get("risk", "1%"),
        mistakes=draft.get("mistakes", ""),
        comments=draft.get("comments", "")
    )


def parse_and_add_trade(text: str, default_year="2026", default_month="Январь", mode="backtest", manual_balance=None):
    """
    Parses a free-form message with trading details, voice transcriptions,
    and TradingView links, and automatically adds it to Notion.
    """
    draft = parse_trade_draft(text, default_year=default_year, default_month=default_month, mode=mode, manual_balance=manual_balance)
    return add_trade_from_draft(draft, target_mode=draft.get("mode", mode))

def get_monthly_stats(year_str="2026", month_str="Январь", mode="backtest"):
    """Query all trades for the specified month and compute comprehensive statistics for backtest, missed, or real mode."""
    if mode == "missed":
        db_id = DB_MISSED_ID
    elif mode == "real":
        db_id = DB_REAL_ID
    else:
        db_id = DB_BACKTEST_ID

    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    filters_list = []
    if year_str:
        y_id = get_relation_id("Year", year_str, mode=mode)
        if y_id:
            filters_list.append({"property": "Year", "relation": {"contains": y_id}})
    if month_str:
        m_id = get_relation_id("Month", month_str, mode=mode)
        if m_id:
            filters_list.append({"property": "Month", "relation": {"contains": m_id}})

    payload = {"page_size": 100, "sorts": [{"property": "Data", "direction": "ascending"}]}
    if len(filters_list) == 1:
        payload["filter"] = filters_list[0]
    elif len(filters_list) > 1:
        payload["filter"] = {"and": filters_list}

    try:
        res = notion_api_request("POST", url, json_payload=payload)
        if res.status_code == 200:
            data = res.json()
            trades = []

            if mode == "missed":
                lookups = LOOKUPS_MISSED
            elif mode == "real":
                lookups = LOOKUPS_REAL
            else:
                lookups = LOOKUPS_BACKTEST
            id_to_name = {item_id: name for cat, cdata in lookups.items() for name, item_id in cdata.get("items", {}).items()}

            for p in data.get("results", []):
                props = p.get("properties", {})
                num_title = props.get("№", {}).get("title", [])
                num = num_title[0].get("plain_text", "") if num_title else ""
                rr_val = props.get("RR", {}).get("number")
                if rr_val is None:
                    continue

                dt = (props.get("Data", {}).get("date") or {}).get("start", "")

                poi_ids = [x["id"] for x in props.get("POI", {}).get("relation", [])]
                poi_names = [id_to_name.get(pid) for pid in poi_ids if pid in id_to_name]
                poi = " + ".join(poi_names) if poi_names else "N/A"

                entry_ids = [x["id"] for x in props.get("Entry", {}).get("relation", [])]
                entry = id_to_name.get(entry_ids[0], "M3") if entry_ids else "M3"

                dir_ids = [x["id"] for x in props.get("Direction", {}).get("relation", [])]
                direction = id_to_name.get(dir_ids[0], "Long") if dir_ids else "Long"

                asset_ids = [x["id"] for x in props.get("Assets", {}).get("relation", [])]
                asset = id_to_name.get(asset_ids[0], "EURUSD") if asset_ids else "EURUSD"

                session_ids = [x["id"] for x in props.get("Session", {}).get("relation", [])]
                session = id_to_name.get(session_ids[0], "London") if session_ids else "London"

                htf_1h = (props.get("HTF_1h", {}) or {}).get("url") or ""
                ltf_3m = (props.get("LTF_3m", {}) or {}).get("url") or ""
                htf_4h = (props.get("HTF_4h", {}) or {}).get("url") or ""

                notes = "".join([t.get("plain_text", "") for t in props.get("Notes", {}).get("rich_text", [])])
                mistakes = "".join([t.get("plain_text", "") for t in props.get("Ошибки", {}).get("rich_text", [])])
                comments = "".join([t.get("plain_text", "") for t in props.get("Комментарии", {}).get("rich_text", [])])
                risk_sel = (props.get("Risk", {}).get("select") or {}).get("name", "1%")

                target_list = [t.get("name", "") for t in props.get("Target", {}).get("multi_select", [])]
                target_str = ", ".join(target_list) if target_list else ""
                profitable = props.get("Profitable", {}).get("checkbox", bool(rr_val > 0.1))

                pnl_val = props.get("PNL", {}).get("number")
                if pnl_val is None:
                    pnl_val = props.get("PNL Amarkets", {}).get("number")

                trades.append({
                    "num": num,
                    "date": dt,
                    "rr": rr_val,
                    "pnl": pnl_val,
                    "asset": asset,
                    "poi": poi,
                    "entry": entry,
                    "direction": direction,
                    "session": session,
                    "htf_1h": htf_1h,
                    "ltf_3m": ltf_3m,
                    "htf_4h": htf_4h,
                    "notes": notes,
                    "mistakes": mistakes,
                    "comments": comments,
                    "risk": risk_sel,
                    "target": target_str,
                    "profitable": profitable,
                    "page_url": p.get("url", "")
                })

            total_trades = len(trades)
            if total_trades == 0:
                return {
                    "mode": mode,
                    "total": 0, "wins": 0, "losses": 0, "be": 0,
                    "winrate": 0.0, "total_r": 0.0, "total_pnl": 0.0, "avg_rr": 0.0,
                    "models": {}, "entries": {}, "trades": []
                }

            wins = sum(1 for t in trades if t["rr"] > 0.1)
            losses = sum(1 for t in trades if t["rr"] < 0)
            be = sum(1 for t in trades if 0 <= t["rr"] <= 0.1)
            total_r = round(sum(t["rr"] for t in trades), 2)
            total_pnl = round(sum(t["pnl"] for t in trades if t.get("pnl") is not None), 2)
            avg_rr = round(total_r / total_trades, 2)
            winrate = round((wins / total_trades) * 100, 1)

            models = {}
            entries = {}
            for t in trades:
                m = t["poi"]
                models[m] = models.get(m, 0) + 1
                e = t["entry"]
                entries[e] = entries.get(e, 0) + 1

            return {
                "mode": mode,
                "total": total_trades,
                "wins": wins,
                "losses": losses,
                "be": be,
                "winrate": winrate,
                "total_r": total_r,
                "total_pnl": total_pnl,
                "avg_rr": avg_rr,
                "models": models,
                "entries": entries,
                "trades": trades
            }
    except Exception as e:
        print(f"Error computing monthly stats ({mode}): {e}")
        return {"error": str(e), "mode": mode}


if __name__ == "__main__":
    print("Testing notion_trader.py with unified properties...")
    s_backtest = get_monthly_stats("2026", "Январь", mode="backtest")
    print(f"Backtest Jan 2026: {s_backtest.get('total')} trades, Winrate: {s_backtest.get('winrate')}%, Total R: {s_backtest.get('total_r')}")
    s_missed = get_monthly_stats("2026", "Сентябрь", mode="missed")
    print(f"Missed Sept 2026: {s_missed.get('total')} trades, Winrate: {s_missed.get('winrate')}%, Total R: {s_missed.get('total_r')}")
    s_real = get_monthly_stats("2026", "Сентябрь", mode="real")
    print(f"Real Sept 2026: {s_real.get('total')} trades, Winrate: {s_real.get('winrate')}%, Total R: {s_real.get('total_r')}")
