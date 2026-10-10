from dotenv import load_dotenv
load_dotenv()
import os
import sys
import re
import html
import json
import urllib.parse
import tempfile
import asyncio
import logging
import http.server
import socketserver
import threading
import subprocess
from datetime import datetime
from pathlib import Path

# Fix httpx bug on Windows with [::1] in NO_PROXY
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
from faster_whisper import WhisperModel
import notion_trader

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TUNNEL_URL = ""

# Load Whisper model once
print("⏳ Загрузка модели распознавания голоса (faster-whisper small)...")
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
print("✅ Модель распознавания речи готова!")

# Disk-backed persistent storage for user sessions
STASH_FILE = Path(__file__).parent / "user_stash.json"

def load_stash_from_disk():
    if STASH_FILE.exists():
        try:
            with open(STASH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k) if str(k).isdigit() else k: v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Error reading stash file: {e}")
    return {}

USER_STASH = load_stash_from_disk()

def save_stash_to_disk():
    try:
        with open(STASH_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_STASH, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving stash file: {e}")

def get_stash(user_id):
    uid = str(user_id)
    if uid not in USER_STASH:
        USER_STASH[uid] = {
            "links": [],
            "year": "2026",
            "month": "Октябрь",
            "mode": "backtest",  # "backtest", "missed", or "real"
            "last_trade": None
        }
        save_stash_to_disk()
    elif "mode" not in USER_STASH[uid]:
        USER_STASH[uid]["mode"] = "backtest"
    if "last_trade" not in USER_STASH[uid] or not USER_STASH[uid]["last_trade"]:
        USER_STASH[uid]["last_trade"] = {
            "page_id": "3ece9b3e-f90a-81a7-9dac-e0793e844a6f",
            "url": "https://app.notion.com/p/1-3ece9b3ef90a81a79dace0793e844a6f",
            "trade_num": "1.",
            "mode": "real"
        }
        save_stash_to_disk()
    return USER_STASH[uid]

def get_mode_name(mode):
    if mode == "missed":
        return "Сделки профукал"
    elif mode == "real":
        return "Реальные сделки"
    return "Бэктесты"

def get_dashboard_url():
    if not TUNNEL_URL:
        return "http://localhost:8585/dashboard.html"
    sep = "&" if "?" in TUNNEL_URL else "?"
    return f"{TUNNEL_URL}/dashboard.html{sep}x-pinggy-no-screen=1"


def get_main_keyboard(user_id):
    stash = get_stash(user_id)
    cur_month = stash.get("month", "Январь")
    cur_year = stash.get("year", "2026")
    mode = stash.get("mode", "backtest")

    if mode == "missed":
        mode_btn_text = "🔄 Режим: 💤 Профукал"
    elif mode == "real":
        mode_btn_text = "🔄 Режим: 💼 Реальные"
    else:
        mode_btn_text = "🔄 Режим: 📊 Бэктесты"

    if TUNNEL_URL:
        web_btn = KeyboardButton("🌐 Web-Терминал", web_app=WebAppInfo(url=get_dashboard_url()))
    else:
        web_btn = KeyboardButton("🌐 Web-Терминал")

    return ReplyKeyboardMarkup(
        [
            [mode_btn_text, web_btn],
            ["📊 Статистика", "📑 Сделки и графики"],
            ["ℹ️ Статус месяца", f"📅 {cur_month} {cur_year}"],
            ["➕ Как добавить сделку"]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

from statsai_server import StatsAIRequestHandler, load_cache, sync_from_notion, start_periodic_sync

def start_web_server(port=8585):
    try:
        load_cache()
        start_periodic_sync(120)
        class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        httpd = ThreadedServer(("", port), StatsAIRequestHandler)
        print(f"🌐 StatsAI Web сервер запущен на http://localhost:{port}")
        httpd.serve_forever()
    except Exception as e:
        print(f"Web server error: {e}")

def update_telegram_menu_button(url):
    try:
        data = json.dumps({
            "menu_button": {
                "type": "web_app",
                "text": "Терминал",
                "web_app": {"url": get_dashboard_url()}
            }
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setChatMenuButton",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
        print(f"✅ Telegram Chat Menu Button синхронизирован: {url}/dashboard.html")
    except Exception as e:
        print(f"Ошибка обновления меню: {e}")

def start_tunnel(port=8585):
    global TUNNEL_URL
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-p", "443", f"-R0:localhost:{port}", "a.pinggy.io"]
    print("🌐 Запуск Pinggy SSH Tunnel для WebApp...")
    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            for line in proc.stdout:
                m = re.search(r"(https://[a-zA-Z0-9-]+\.(?:free\.pinggy\.net|run\.pinggy-free\.link))", line)
                if m:
                    new_url = m.group(1).strip()
                    if new_url != TUNNEL_URL:
                        TUNNEL_URL = new_url
                        print(f"🚀 Telegram WebApp HTTPS URL (Pinggy): {TUNNEL_URL}")
                        with open("tunnel_url.txt", "w") as f:
                            f.write(TUNNEL_URL)
                        update_telegram_menu_button(TUNNEL_URL)
            proc.wait()
        except Exception as e:
            print(f"Tunnel error: {e}")
            time.sleep(5)

MONTHS_LIST = [
    ["Январь", "Февраль", "Март"],
    ["Апрель", "Май", "Июнь"],
    ["Июль", "Август", "Сентябрь"],
    ["Октябрь", "Ноябрь", "Декабрь"]
]

def get_months_inline_keyboard():
    keyboard = []
    for row in MONTHS_LIST:
        kb_row = [InlineKeyboardButton(m, callback_data=f"set_month:{m}") for m in row]
        keyboard.append(kb_row)
    return InlineKeyboardMarkup(keyboard)

def format_draft_card(draft):
    asset = draft.get("asset", "EURUSD")
    direction = draft.get("direction", "Long")
    dir_emoji = "🟢" if direction == "Long" else "🔴"
    date_str = draft.get("date_str", "")
    month = draft.get("month", "")
    year = draft.get("year", "")
    session = draft.get("session", "London")
    tf = draft.get("timeframe", "M3")
    models = draft.get("model", [])
    poi_str = ", ".join(models) if isinstance(models, list) else str(models)
    targets = draft.get("target_tags", [])
    tgt_str = ", ".join(targets) if targets else "Не указан"
    risk_str = draft.get("risk", "1%")
    risk_cash = draft.get("risk_cash", 100.0)
    balance = draft.get("balance", 10000.0)
    raw_rr = draft.get("raw_rr", 2.0)
    eff_rr = draft.get("rr", 2.0)
    pnl_d = draft.get("pnl_dollar", 0.0)
    pnl_sign = "+" if pnl_d > 0 else ("" if pnl_d == 0 else "-")
    notes = draft.get("notes", "")
    mistakes = draft.get("mistakes", "")
    comments = draft.get("comments", "")
    mode = draft.get("mode", "backtest")
    mode_name = get_mode_name(mode)

    lines = [
        "📋 <b>ЧЕРНОВИК СДЕЛКИ</b> (Проверь перед записью):",
        "",
        f"📌 <b>Инструмент:</b> {html.escape(asset)} • {dir_emoji} <b>{direction}</b>",
        f"📅 <b>Дата:</b> <code>{date_str}</code> ({html.escape(month)} {year})",
        f"⏰ <b>Сессия:</b> {html.escape(session)}",
        f"🎯 <b>POI:</b> {html.escape(poi_str)} (BoS {tf})",
        f"🎯 <b>Таргет:</b> {html.escape(tgt_str)}",
        f"🛡 <b>Риск:</b> <code>{risk_str}</code> (<b>${risk_cash:g}</b> от баланса ${balance:g})",
        f"💰 <b>Результат:</b> <code>{'+' if eff_rr > 0 else ''}{eff_rr:g}R</code> (<b>{pnl_sign}${abs(pnl_d):g}</b>)"
    ]
    if abs(raw_rr - eff_rr) > 0.001:
        lines.append(f"   <i>(базовый {raw_rr:g}RR пересчитан под риск {risk_str})</i>")
    if notes:
        lines.append(f"📝 <b>Тезис:</b> <i>{html.escape(notes)}</i>")
    if mistakes:
        lines.append(f"⚠️ <b>Ошибка:</b> <i>{html.escape(mistakes)}</i>")
    if comments:
        lines.append(f"💬 <b>Комментарий:</b> <i>{html.escape(comments)}</i>")
    lines.append("")
    lines.append(f"📍 Предварительный режим: <b>«{mode_name}»</b>")
    lines.append("<i>Куда сохранить сделку? Нажми кнопку ниже:</i>")
    return "\n".join(lines)

def get_draft_keyboard(draft):
    direction = draft.get("direction", "Long")
    dir_toggle_text = "🔴 В Шорт 🔄" if direction == "Long" else "🟢 В Лонг 🔄"
    risk_str = draft.get("risk", "1%")
    session = draft.get("session", "London")

    keyboard = [
        [
            InlineKeyboardButton("💼 Сохранить в Реальные", callback_data="draft:save_real"),
            InlineKeyboardButton("📊 Сохранить в Бэктест", callback_data="draft:save_bt")
        ],
        [
            InlineKeyboardButton(f"🛡 Риск: {risk_str} 🔄", callback_data="draft:cycle_risk"),
            InlineKeyboardButton(f"{dir_toggle_text}", callback_data="draft:toggle_dir")
        ],
        [
            InlineKeyboardButton(f"⏰ Сессия: {session} 🔄", callback_data="draft:cycle_session"),
            InlineKeyboardButton("💤 В Профукал", callback_data="draft:save_missed")
        ],
        [
            InlineKeyboardButton("❌ Отменить", callback_data="draft:cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    mode = stash.get("mode", "backtest")
    mode_name = get_mode_name(mode)
    print(f"👤 Пользователь {user_id} вызвал /start (режим: {mode})")

    link_text = f"\n🌐 **Прямая ссылка:** {get_dashboard_url()}\n" if TUNNEL_URL else ""
    msg = (
        "👋 **Привет! Я твой торговый ассистент для Notion.**\n\n"
        f"Текущий режим: **{mode_name}**\n"
        f"{link_text}\n"
        "🎯 **Кнопки управления внизу экрана:**\n"
        "• 🔄 **Режим** — переключение между **Бэктестами**, **Сделками профукал** и **Реальными сделками**\n"
        "• 📊 **Статистика** — расчет PnL, винрейта и метрик за активный месяц\n"
        "• 🌐 **Web-Терминал** — интерактивный терминал с графиками эквити в Telegram (Mini App)\n"
        "• 📑 **Сделки и графики** — журнал сделок со ссылками на TradingView\n"
        "• 📅 **Выбор месяца** — быстрое переключение активного периода\n"
        "• ℹ️ **Статус месяца** — проверка базы и номер следующей сделки\n"
        "• ➕ **Как добавить сделку** — подсказка по быстрой записи"
    )
    inline_kb = None
    if TUNNEL_URL:
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Открыть Web-Терминал", web_app=WebAppInfo(url=get_dashboard_url()))]
        ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=inline_kb)
    # Also refresh main reply keyboard
    await update.message.reply_text("Клавиатура обновлена! 👇", reply_markup=get_main_keyboard(user_id))

async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    current_mode = stash.get("mode", "backtest")

    args = context.args
    if args:
        arg = args[0].lower()
        if "реал" in arg or "real" in arg or "live" in arg:
            new_mode = "real"
        elif "профук" in arg or "miss" in arg:
            new_mode = "missed"
        else:
            new_mode = "backtest"
    else:
        # 3-way cycle: backtest -> missed -> real -> backtest
        if current_mode == "backtest":
            new_mode = "missed"
        elif current_mode == "missed":
            new_mode = "real"
        else:
            new_mode = "backtest"

    stash["mode"] = new_mode

    if new_mode == "real":
        msg = (
            "💼 **Переключено на режим: РЕАЛЬНЫЕ СДЕЛКИ (LIVE)!**\n\n"
            "• Новые голосовые сообщения, текст и ссылки теперь заносятся в базу **«Реальные сделки»**.\n"
            "• Бот фиксирует: **фактический вход**, результат в R, эмоции и сетап на твоем счете.\n"
            "• Кнопка переключена: `[🔄 Режим: 💼 Реальные]`"
        )
    elif new_mode == "missed":
        msg = (
            "💤 **Переключено на режим: СДЕЛКИ ПРОФУКАЛ!**\n\n"
            "• Новые голосовые сообщения, текст и ссылки теперь заносятся в базу **«Сделки профукал»**.\n"
            "• Бот фиксирует: **отработала ли твоя идея в тейк**, сколько R упущено и причину пропуска.\n"
            "• Кнопка переключена: `[🔄 Режим: 💤 Профукал]`"
        )
    else:
        msg = (
            "📊 **Переключено на режим: БЭКТЕСТЫ!**\n\n"
            "• Новые сделки и статистика привязаны к основной базе **бэктестов** стратегии SMC 1H3M.\n"
            "• Кнопка переключена: `[🔄 Режим: 📊 Бэктесты]`"
        )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    mode = stash.get("mode", "real")
    args = context.args

    if args:
        arg_val = args[0].strip().lower()
        if arg_val in ("reset", "сброс", "авто"):
            stash.pop("manual_balance", None)
            save_stash_to_disk()
            bal = notion_trader.get_account_balance(mode=mode)
            r_label, r_float, _, status = notion_trader.get_recommended_risk(balance=bal, mode=mode)
            risk_cash = round(bal * (r_float / 100.0), 2)
            await update.message.reply_text(
                f"🔄 <b>Ручной баланс сброшен!</b>\n\n"
                f"• Текущий баланс из Notion: <b>${bal:g}</b>\n"
                f"• Фаза: <b>{status}</b>\n"
                f"• Рекомендуемый риск: <code>{r_label}</code> (<b>${risk_cash:g}</b>)",
                parse_mode="HTML",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        else:
            try:
                clean_num = float(re.sub(r"[^\d.]", "", arg_val.replace(",", ".")))
                if clean_num > 0:
                    stash["manual_balance"] = clean_num
                    save_stash_to_disk()
                    r_label, r_float, _, status = notion_trader.get_recommended_risk(balance=clean_num, mode=mode, manual_balance=clean_num)
                    risk_cash = round(clean_num * (r_float / 100.0), 2)
                    await update.message.reply_text(
                        f"✅ <b>Баланс депозита установлен: ${clean_num:g}</b>\n\n"
                        f"• Фаза риска: <b>{status}</b>\n"
                        f"• Рекомендуемый риск: <code>{r_label}</code> (<b>${risk_cash:g}</b> за сделку)\n\n"
                        f"<i>Все новые сделки будут рассчитывать риск от ${clean_num:g}.</i>",
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard(user_id)
                    )
                    return
            except Exception:
                pass

    cur_bal = notion_trader.get_account_balance(mode=mode, manual_balance=stash.get("manual_balance"))
    r_label, r_float, _, status = notion_trader.get_recommended_risk(balance=cur_bal, mode=mode, manual_balance=stash.get("manual_balance"))
    risk_cash = round(cur_bal * (r_float / 100.0), 2)
    is_manual = " (установлен вручную)" if stash.get("manual_balance") else " (рассчитан из базы Notion)"

    msg = (
        f"💰 <b>Управление депозитом и риском:</b>\n\n"
        f"• Текущий капитал: <b>${cur_bal:g}</b>{is_manual}\n"
        f"• Фаза счета: <b>{status}</b>\n"
        f"• Рекомендуемый риск: <code>{r_label}</code> (<b>${risk_cash:g}</b> за сделку)\n\n"
        f"📌 <b>Градации риска стратегии:</b>\n"
        f"  🔴 &lt; $9,700 — <b>0.5%</b> ($50) <i>(Просадка)</i>\n"
        f"  🟡 $9,700 .. $10,199 — <b>1.0%</b> ($100) <i>(Базовый риск)</i>\n"
        f"  🟢 ≥ $10,200 — <b>2.0%</b> ($200) <i>(Разгон прибыли, кап 2%)</i>\n\n"
        f"💡 <i>Чтобы задать баланс вручную:</i> <code>/balance 10000</code>\n"
        f"💡 <i>Чтобы вернуть авто-расчет из Notion:</i> <code>/balance reset</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    year = stash.get("year", "2026")
    month = stash.get("month", "Январь")
    mode = stash.get("mode", "backtest")

    wait_msg = await update.message.reply_text(f"⏳ Считаю статистику из Notion ({mode})...")
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, lambda: notion_trader.get_monthly_stats(year, month, mode=mode))

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if stats.get("error"):
        await update.message.reply_text(f"❌ Ошибка Notion: {stats['error']}", reply_markup=get_main_keyboard(user_id))
        return

    total = stats["total"]
    mode_title = get_mode_name(mode).upper()
    if total == 0:
        await update.message.reply_text(f"В режиме «{mode_title}» за {month} {year} пока нет сохраненных сделок.", reply_markup=get_main_keyboard(user_id))
        return

    wins = stats["wins"]
    losses = stats["losses"]
    be = stats["be"]
    winrate = stats["winrate"]
    total_r = stats["total_r"]
    avg_rr = stats["avg_rr"]

    models_str = "\n".join([f"  • {html.escape(m)}: {cnt} сд." for m, cnt in stats["models"].items()])
    entries_str = "\n".join([f"  • {html.escape(e)}: {cnt} сд." for e, cnt in stats["entries"].items()])

    pnl_sign = "+" if total_r > 0 else ""
    total_pnl = stats.get("total_pnl", 0.0)
    pnl_dollar_str = f" / <code>{'+' if total_pnl > 0 else ''}{total_pnl:g}$</code>" if total_pnl != 0 else ""

    if mode == "real":
        pnl_emoji = "🟢" if total_r > 0 else "🔴"
        msg = (
            f"💼 <b>РЕАЛЬНАЯ ТОРГОВЛЯ (LIVE): {html.escape(month.upper())} {html.escape(year)}</b>\n\n"
            f"{pnl_emoji} <b>Реализованный PnL:</b> <code>{pnl_sign}{total_r} R</code>{pnl_dollar_str}\n"
            f"🎯 <b>Винрейт на реале:</b> <code>{winrate}%</code> (Побед: {wins} | Убытков: {losses} | BE: {be})\n"
            f"⚖️ <b>Средний RR сделки:</b> <code>{avg_rr} R</code>\n"
            f"🔢 <b>Всего сделок на счете:</b> <code>{total}</code>\n\n"
            f"🏆 <b>Модели сетапов:</b>\n{models_str}\n\n"
            f"⏱️ <b>Таймфреймы BoS:</b>\n{entries_str}\n\n"
            f"🔥 <i>Отличная дисциплина и контроль риска на реальном счете!</i>"
        )
    elif mode == "missed":
        msg = (
            f"💤 <b>АНАЛИЗ ПРОПУЩЕННЫХ СДЕЛОК: {html.escape(month.upper())} {html.escape(year)}</b>\n\n"
            f"🎯 <b>Точность рыночной логики (Винрейт):</b> <code>{winrate}%</code>\n"
            f"• Дошли до TP: <b>{wins}</b> | Выбило бы SL: <b>{losses}</b> | BE: <b>{be}</b>\n\n"
            f"💸 <b>Упущенный потенциальный профит:</b> <code>{pnl_sign}{total_r} R</code>\n"
            f"⚖️ <b>Средний потенциал сетапа:</b> <code>{avg_rr} R</code>\n"
            f"🔢 <b>Всего пропущено сетапов:</b> <code>{total}</code>\n\n"
            f"🏆 <b>Использованные модели:</b>\n{models_str}\n\n"
            f"⏱️ <b>Таймфреймы входа:</b>\n{entries_str}\n\n"
            f"💡 <i>Анализ: твоя рыночная логика верна в {winrate}% случаев. Устраняй барьеры к открытию и забирай этот профит!</i>"
        )
    else:
        pnl_emoji = "🟢" if total_r > 0 else "🔴"
        msg = (
            f"📊 <b>КВАНТ-СТАТИСТИКА БЭКТЕСТОВ: {html.escape(month.upper())} {html.escape(year)}</b>\n\n"
            f"{pnl_emoji} <b>Накопленный результат:</b> <code>{pnl_sign}{total_r} R</code>\n"
            f"🎯 <b>Винрейт:</b> <code>{winrate}%</code> (Побед: {wins} | Убытков: {losses} | BE: {be})\n"
            f"⚖️ <b>Средний RR:</b> <code>{avg_rr} R</code> на сделку\n"
            f"🔢 <b>Всего сделок:</b> <code>{total}</code>\n\n"
            f"🏆 <b>Модели входа (POI):</b>\n{models_str}\n\n"
            f"⏱️ <b>Таймфреймы BoS:</b>\n{entries_str}"
        )

    inline_kb = None
    if TUNNEL_URL:
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Открыть Web-Терминал прямо в Telegram", web_app=WebAppInfo(url=get_dashboard_url()))]
        ])

    try:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=inline_kb)
    except Exception:
        await update.message.reply_text(re.sub(r"<[^>]+>", "", msg), reply_markup=inline_kb)

async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    year = stash.get("year", "2026")
    month = stash.get("month", "Январь")
    mode = stash.get("mode", "backtest")

    wait_msg = await update.message.reply_text(f"⏳ Загружаю сделки из Notion ({mode})...")
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, lambda: notion_trader.get_monthly_stats(year, month, mode=mode))

    try:
        await wait_msg.delete()
    except Exception:
        pass

    trades = stats.get("trades", [])
    mode_name = get_mode_name(mode)
    if not trades:
        await update.message.reply_text(f"В категории «{mode_name}» за {month} {year} пока нет сохраненных сделок.", reply_markup=get_main_keyboard(user_id))
        return

    if mode == "real":
        icon = "💼"
        status_prefix = "Тезис"
    elif mode == "missed":
        icon = "💤"
        status_prefix = "💤 Пропущено"
    else:
        icon = "📖"
        status_prefix = "Тезис"

    lines = [f"{icon} <b>Сделки [{mode_name.upper()}]: {html.escape(month.upper())} {html.escape(year)}</b>\n"]
    for t in trades:
        num = t.get("num", "")
        dt = t.get("date", "")
        direction = t.get("direction", "")
        rr = t.get("rr", "")
        asset = t.get("asset", "EURUSD")
        poi = t.get("poi", "")
        entry = t.get("entry", "M3")
        h1 = t.get("htf_1h", "")
        m3 = t.get("ltf_3m", "")
        notes = t.get("notes", "")

        links_part = []
        if h1:
            links_part.append(f"<a href='{h1}'>График 1H ↗</a>")
        if m3:
            links_part.append(f"<a href='{m3}'>График 3M/1M ↗</a>")
        links_str = " | ".join(links_part) if links_part else "ссылок нет"

        pnl = t.get("pnl")
        pnl_str = f" (<code>{'+' if pnl > 0 else ''}{pnl:g}$</code>)" if pnl is not None else ""
        try:
            rr_num = float(rr)
            rr_sign = "+" if rr_num > 0 else ""
        except Exception:
            rr_sign = ""

        extra_info = []
        if t.get("risk"):
            extra_info.append(f"🛡 <i>Риск:</i> {html.escape(t['risk'])}")
        if t.get("mistakes"):
            extra_info.append(f"⚠️ <i>Ошибка:</i> {html.escape(t['mistakes'])}")
        if t.get("comments"):
            extra_info.append(f"💬 <i>Комментарий:</i> {html.escape(t['comments'])}")
        extra_str = ("\n" + "\n".join(extra_info)) if extra_info else ""

        lines.append(
            f"<b>Сделка #{num} ({dt})</b> • {asset} {direction} • <code>{rr_sign}{rr}R</code>{pnl_str}\n"
            f"🎯 <i>Модель:</i> {html.escape(poi)} (BoS {entry})\n"
            f"🔗 <b>TradingView:</b> {links_str}\n"
            f"📝 <i>{status_prefix}:</i> {html.escape(notes)}{extra_str}\n"
        )

    msg = "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if TUNNEL_URL:
        msg = (
            "🌐 **Интерактивный Web-терминал SMC 1H3M**\n\n"
            f"🔗 **Прямая ссылка:** {get_dashboard_url()}\n\n"
            "Нажми на кнопку ниже, чтобы открыть терминал **прямо внутри Telegram** (Mini App):\n"
            "• Три вкладки: **📊 Бэктесты**, **💤 Пропущенные сделки** и **💼 Реальные сделки**\n"
            "• Интерактивная кривая эквити\n"
            "• Анализ моделей и таймфреймов\n"
            "• Журнал сделок со ссылками на TradingView\n"
            "• Чеклист правил стратегии"
        )
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Открыть Терминал в Telegram", web_app=WebAppInfo(url=get_dashboard_url()))]
        ])
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=inline_kb)
    else:
        msg = (
            "🌐 **Терминал запущен локально:**\n\n"
            "Прямая ссылка: http://localhost:8585"
        )
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(user_id))

async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    args = context.args

    if not args:
        msg = f"Текущий рабочий месяц: **{stash['month']} {stash['year']}**.\n\nВыбери новый месяц:"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_months_inline_keyboard())
        return

    month_arg = args[0].capitalize()
    year_arg = args[1] if len(args) > 1 else stash["year"]
    stash["month"] = month_arg
    stash["year"] = year_arg

    await update.message.reply_text(
        f"✅ Установлен рабочий период: **{month_arg} {year_arg}**.\nКнопка внизу обновлена!",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user_id)
    )

async def month_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "attach_links_to_last":
        user_id = update.effective_user.id
        stash = get_stash(user_id)
        last_trade = stash.get("last_trade")
        links = stash.get("links", [])
        if not last_trade or not links:
            await query.edit_message_text("❌ В буфере нет ссылок для прикрепления.")
            return

        page_id = last_trade.get("page_id")
        trade_num = str(last_trade.get("trade_num", "1")).rstrip(".")
        res = notion_trader.update_trade_links(page_id, links)
        if res.get("success"):
            stash["links"] = []
            save_stash_to_disk()
            await query.edit_message_text(
                f"✅ **Графики и скриншоты успешно прикреплены к сделке #{trade_num}!**\n\n"
                f"🔗 [Открыть сделку в Notion]({last_trade.get('url')})",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(f"❌ Ошибка Notion: {res.get('error')}")
        return

    if data.startswith("edit_trade:"):
        trade_num = data.split(":")[1]
        user_id = update.effective_user.id
        stash = get_stash(user_id)
        stash["awaiting_correction"] = stash.get("last_trade", {}).get("page_id", True)
        save_stash_to_disk()
        await query.message.reply_text(
            f"✍️ **Редактирование сделки #{trade_num}:**\n\n"
            f"Надиктуй **голосом** или напиши **текстом**, что именно нужно изменить:\n"
            f"• Например: _«Не евро, а SP500, число 5 октября 2026 года, таргет low NY»_\n"
            f"• Или: _«Моя ошибка была в том, что не дождался снятия второго FVG»_\n\n"
            f"Бот автоматически исправит параметры в Notion и обновит сайт StatsAI!",
            parse_mode="Markdown"
        )
        return

    if data.startswith("set_month:"):
        selected_month = data.split(":")[1]
        user_id = update.effective_user.id
        stash = get_stash(user_id)
        stash["month"] = selected_month
        save_stash_to_disk()
        year = stash["year"]

        await query.edit_message_text(
            f"✅ Рабочий период переключен на **{selected_month} {year}**!\n"
            f"Все новые сделки и статистика теперь привязаны к этому месяцу."
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Кнопка обновлена: [📅 {selected_month} {year}]",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    if data.startswith("draft:"):
        action = data.split(":")[1]
        user_id = update.effective_user.id
        stash = get_stash(user_id)
        draft = stash.get("active_draft")

        if not draft:
            await query.edit_message_text("❌ Черновик сделки устарел или был отменен.")
            return

        if action == "cancel":
            stash.pop("active_draft", None)
            stash["links"] = []
            save_stash_to_disk()
            await query.edit_message_text("❌ <b>Черновик отменен.</b> Сделка не сохранена в Notion.", parse_mode="HTML")
            return

        if action == "cycle_risk":
            curr_risk = draft.get("risk", "1%")
            cycle = ["0.5%", "1%", "1.5%", "2%"]
            next_idx = (cycle.index(curr_risk) + 1) % len(cycle) if curr_risk in cycle else 0
            new_risk = cycle[next_idx]
            draft["risk"] = new_risk
            r_num = notion_trader.parse_risk_val(new_risk)
            draft["risk_float"] = r_num
            bal = draft.get("balance", 10000.0)
            draft["risk_cash"] = round(bal * (r_num / 100.0), 2)
            raw_rr = draft.get("raw_rr", 2.0)
            eff_rr, calc_pnl, _ = notion_trader.compute_effective_rr_and_pnl(raw_rr, r_num, pnl_dollar=None, deposit=bal)
            draft["rr"] = eff_rr
            draft["pnl_dollar"] = calc_pnl
            save_stash_to_disk()
            await query.edit_message_text(format_draft_card(draft), parse_mode="HTML", reply_markup=get_draft_keyboard(draft))
            return

        if action == "toggle_dir":
            cur_dir = draft.get("direction", "Long")
            new_dir = "Short" if cur_dir == "Long" else "Long"
            draft["direction"] = new_dir
            draft["trend"] = "downtrend" if new_dir == "Short" else "uptrend"
            save_stash_to_disk()
            await query.edit_message_text(format_draft_card(draft), parse_mode="HTML", reply_markup=get_draft_keyboard(draft))
            return

        if action == "cycle_session":
            cur_sess = draft.get("session", "London")
            sess_cycle = ["London", "NY", "Frankfurt", "Asia"]
            next_idx = (sess_cycle.index(cur_sess) + 1) % len(sess_cycle) if cur_sess in sess_cycle else 0
            new_sess = sess_cycle[next_idx]
            draft["session"] = new_sess
            time_map = {"Frankfurt": "09:15:00", "London": "10:00:00", "NY": "15:45:00", "Asia": "03:30:00"}
            if new_sess in time_map:
                draft["entry_time"] = time_map[new_sess]
                if "date_str" in draft:
                    date_prefix = draft["date_str"][:10]
                    draft["date_str"] = f"{date_prefix} {time_map[new_sess]}"
            save_stash_to_disk()
            await query.edit_message_text(format_draft_card(draft), parse_mode="HTML", reply_markup=get_draft_keyboard(draft))
            return

        if action in ("save_real", "save_bt", "save_missed"):
            target_mode = "real" if action == "save_real" else ("backtest" if action == "save_bt" else "missed")
            target_title = "«Реальные сделки»" if target_mode == "real" else ("«Бэктесты»" if target_mode == "backtest" else "«Сделки профукал»")
            
            await query.edit_message_text(f"⏳ Сохраняю сделку в базу {target_title}...")
            
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None,
                lambda: notion_trader.add_trade_from_draft(draft, target_mode=target_mode)
            )

            if res.get("success"):
                stash["active_draft"] = None
                stash["links"] = []
                trade_num = res.get("trade_num")
                page_url = res.get("url")
                page_id = res.get("page_id")
                stash["mode"] = target_mode
                stash["last_trade"] = {
                    "page_id": page_id,
                    "url": page_url,
                    "trade_num": trade_num,
                    "mode": target_mode
                }
                if draft.get("year"):
                    stash["year"] = draft.get("year")
                if draft.get("month"):
                    stash["month"] = draft.get("month")
                save_stash_to_disk()

                t_num_disp = str(trade_num).rstrip(".")
                eff_rr = draft.get("rr", 2.0)
                rr_disp = f"{'+' if eff_rr > 0 else ''}{eff_rr:g}R"
                pnl_d = draft.get("pnl_dollar", 0.0)
                pnl_str = f" • {'+' if pnl_d > 0 else ''}${pnl_d:g}" if pnl_d else ""
                
                reply = (
                    f"🎉 <b>Сделка #{t_num_disp} успешно добавлена в {target_title}!</b>\n\n"
                    f"📌 <b>Пара:</b> {html.escape(draft.get('asset', ''))} • <b>{draft.get('direction', '')}</b>\n"
                    f"📅 <b>Дата:</b> <code>{draft.get('date_str', '')}</code>\n"
                    f"⏰ <b>Сессия:</b> {html.escape(draft.get('session', ''))}\n"
                    f"🛡 <b>Риск:</b> <code>{draft.get('risk', '1%')}</code> (${draft.get('risk_cash', 100):g})\n"
                    f"💰 <b>Результат:</b> <code>{rr_disp}</code>{pnl_str}\n"
                    f"📝 <b>Тезис:</b> <i>{html.escape(draft.get('notes', ''))}</i>\n\n"
                    f"🔗 <a href='{page_url}'>Открыть сделку в Notion</a>"
                )

                buttons = []
                if TUNNEL_URL:
                    buttons.append([InlineKeyboardButton("⚡ Открыть Терминал", web_app=WebAppInfo(url=get_dashboard_url()))])
                buttons.append([InlineKeyboardButton("✏️ Исправить сделку", callback_data=f"edit_trade:{t_num_disp}")])
                inline_kb = InlineKeyboardMarkup(buttons)

                threading.Thread(target=sync_from_notion, daemon=True).start()
                await query.edit_message_text(reply, parse_mode="HTML", reply_markup=inline_kb)
            else:
                err_text = f"❌ Ошибка Notion: {res.get('error')}"
                await query.edit_message_text(err_text)
            return

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    mode = stash.get("mode", "backtest")
    mode_name = get_mode_name(mode)
    next_num = notion_trader.get_next_trade_number(year_str=stash["year"], month_str=stash["month"], mode=mode)
    links_count = len(stash.get("links", []))
    msg = (
        f"📊 **Статус интеграции Notion:**\n"
        f"• Текущий режим: **{mode_name}**\n"
        f"• Связь с базой: ✅ Подключено\n"
        f"• Активный период: **{stash['month']} {stash['year']}**\n"
        f"• Следующий номер сделки: **#{next_num}**\n"
        f"• Сохраненных ссылок в буфере: {links_count}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in USER_STASH:
        USER_STASH[user_id]["links"] = []
    await update.message.reply_text("🧹 Буфер ссылок очищен.", reply_markup=get_main_keyboard(user_id))

def extract_tv_links(text: str):
    return re.findall(r"https?://(?:www\.)?tradingview\.com/x/[A-Za-z0-9]+/?", text)


async def handle_trade_correction(text: str, update: Update, audio_transcribed=False):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    mode = stash.get("mode", "real")
    last_trade = stash.get("last_trade")

    status_msg = None
    try:
        status_msg = await send_msg_with_retry(update.message.reply_text, "⏳ Исправляю сделку в Notion и синхронизирую с сайтом StatsAI...")
    except Exception as e:
        logger.warning(f"Could not send status_msg: {e}")

    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None,
            lambda: notion_trader.parse_and_update_trade(
                text,
                last_trade_info=last_trade,
                mode=mode
            )
        )

        if res.get("success"):
            stash.pop("awaiting_correction", None)
            trade_num = res.get("trade_num")
            page_url = res.get("url")
            page_id = res.get("page_id")
            saved_mode = res.get("mode", mode)
            stash["mode"] = saved_mode
            updates = res.get("updates", {})

            if updates.get("date"):
                d_val = updates["date"]
                try:
                    dt_obj = datetime.strptime(d_val[:10], "%Y-%m-%d")
                    stash["year"] = str(dt_obj.year)
                    stash["month"] = notion_trader.MONTH_NAMES_RU.get(dt_obj.month, "Октябрь")
                except Exception:
                    pass

            stash["last_trade"] = {
                "page_id": page_id,
                "url": page_url,
                "trade_num": trade_num,
                "mode": saved_mode
            }
            save_stash_to_disk()

            # Trigger immediate sync with website
            threading.Thread(target=sync_from_notion, daemon=True).start()

            details_lines = []
            if updates.get("mode"):
                details_lines.append(f"🔄 **База:** {updates['mode']}")
            elif saved_mode != mode:
                details_lines.append(f"🔄 **База:** {get_mode_name(saved_mode)}")
            if updates.get("date"):
                details_lines.append(f"📅 **Дата:** `{updates['date']}` ({stash.get('month')} {stash.get('year')})")
            if updates.get("asset") or updates.get("direction"):
                a_str = updates.get("asset", "")
                d_str = updates.get("direction", "")
                details_lines.append(f"📌 **Пара / Сетап:** {a_str} {d_str}".strip())
            if updates.get("poi"):
                poi_s = ", ".join(updates["poi"]) if isinstance(updates["poi"], list) else str(updates["poi"])
                e_s = updates.get("entry", "M3")
                details_lines.append(f"🎯 **POI:** {poi_s} (BoS {e_s})")
            if updates.get("targets"):
                t_s = ", ".join(updates["targets"]) if isinstance(updates["targets"], list) else str(updates["targets"])
                details_lines.append(f"🎯 **Таргет:** {t_s}")
            if updates.get("session"):
                details_lines.append(f"⏰ **Сессия:** {updates['session']}")
            if updates.get("risk"):
                r_val_disp = updates['risk']
                r_num = notion_trader.parse_risk_val(r_val_disp)
                risk_cash = round(10000.0 * (r_num / 100.0), 2)
                details_lines.append(f"🛡 **Риск:** `{r_val_disp}` (${risk_cash:g} от $10,000)")
            if updates.get("rr") is not None:
                rr_v = updates["rr"]
                rr_sign = "+" if rr_v > 0 else ""
                pnl_str = ""
                if updates.get("pnl_dollar") is not None:
                    pnl_d = updates["pnl_dollar"]
                    pnl_sign = "+" if pnl_d > 0 else "-"
                    pnl_str = f" • **{pnl_sign}${abs(pnl_d):g}**"
                raw_rr_info = ""
                raw_rr_val = updates.get("raw_rr")
                if raw_rr_val is not None and abs(float(raw_rr_val) - float(rr_v)) > 0.001:
                    raw_rr_sign = "+" if float(raw_rr_val) > 0 else ""
                    raw_rr_info = f" _(базовый {raw_rr_sign}{float(raw_rr_val):g}RR)_"
                details_lines.append(f"💰 **Результат:** `{rr_sign}{rr_v:g}R`{pnl_str}{raw_rr_info}")
            if updates.get("notes"):
                details_lines.append(f"📝 **Тезис:** _{updates['notes']}_")
            if updates.get("mistakes"):
                details_lines.append(f"⚠️ **Ошибка:** _{updates['mistakes']}_")
            if updates.get("comments"):
                details_lines.append(f"💬 **Комментарий:** _{updates['comments']}_")

            card_str = "\n".join(details_lines) if details_lines else "Параметры успешно обновлены."
            t_num_disp = str(trade_num).rstrip(".")

            reply = (
                f"✏️ **Сделка #{t_num_disp} успешно исправлена!**\n\n"
                f"{card_str}\n\n"
                f"✅ **Синхронизировано:** данные мгновенно обновлены в Notion и на сайте StatsAI.\n"
                f"🔗 [Открыть сделку в Notion]({page_url})"
            )
            if audio_transcribed:
                reply = f"🎙 *Распознано:* «{text.strip()}»\n\n" + reply

            buttons = []
            if TUNNEL_URL:
                buttons.append([InlineKeyboardButton("⚡ Открыть Терминал", web_app=WebAppInfo(url=get_dashboard_url()))])
            buttons.append([InlineKeyboardButton("✏️ Исправить еще раз", callback_data=f"edit_trade:{t_num_disp}")])
            inline_kb = InlineKeyboardMarkup(buttons)

            if status_msg:
                try:
                    await send_msg_with_retry(status_msg.edit_text, reply, parse_mode="Markdown", reply_markup=inline_kb)
                except Exception:
                    await send_msg_with_retry(update.message.reply_text, reply, parse_mode="Markdown", reply_markup=inline_kb)
            else:
                await send_msg_with_retry(update.message.reply_text, reply, parse_mode="Markdown", reply_markup=inline_kb)
        else:
            err_text = f"❌ Ошибка обновления сделки: {res.get('error')}"
            if status_msg:
                try:
                    await send_msg_with_retry(status_msg.edit_text, err_text)
                except Exception:
                    await send_msg_with_retry(update.message.reply_text, err_text)
            else:
                await send_msg_with_retry(update.message.reply_text, err_text)
    except Exception as e:
        logger.exception("Error correcting trade")
        err_msg = f"❌ Ошибка обработки исправления: {str(e)}"
        if status_msg:
            try:
                await send_msg_with_retry(status_msg.edit_text, err_msg)
            except Exception:
                await send_msg_with_retry(update.message.reply_text, err_msg)
        else:
            await send_msg_with_retry(update.message.reply_text, err_msg)


async def handle_trade_text(text: str, update: Update, audio_transcribed=False):
    user_id = update.effective_user.id
    stash = get_stash(user_id)
    mode = stash.get("mode", "backtest")

    new_links = extract_tv_links(text)
    for l in new_links:
        if l not in stash["links"]:
            stash["links"].append(l)
    if new_links:
        save_stash_to_disk()

    text_without_links = re.sub(r"https?://\S+", "", text).strip()
    if not text_without_links and len(new_links) > 0 and not audio_transcribed:
        count = len(stash["links"])
        last_trade = stash.get("last_trade")
        inline_buttons = []
        last_trade_text = ""
        if last_trade:
            t_num = str(last_trade.get("trade_num", "1")).rstrip(".")
            inline_buttons.append([
                InlineKeyboardButton(f"🔗 Прикрепить к сделке #{t_num}", callback_data="attach_links_to_last")
            ])
            last_trade_text = f"\n• Либо нажми кнопку ниже, чтобы прикрепить эти ссылки к сделке **#{t_num}**."
        inline_kb = InlineKeyboardMarkup(inline_buttons) if inline_buttons else None

        await update.message.reply_text(
            f"📎 Ссылки сохранены в буфер ({count} шт.).\n\n"
            f"• Надиктуй **голосом** или напиши **текстом** параметры новой сделки (например: _EURUSD шорт 2R снятие 0.5 FVG_),{last_trade_text}",
            reply_markup=inline_kb or get_main_keyboard(user_id),
            parse_mode="Markdown"
        )
        return

    has_new_links = bool(stash.get("links"))
    is_mode_switch_phrase = bool(re.search(
        r"\b(?:это\s+|перенеси\s+|занеси\s+|должн\w+\s+быть\s+)?(?:в|на)\s*(?:бэктест\w*|реал\w*|профукал\w*)\b|\b(?:почему.*(?:реальн\w*|бэктест\w*))\b",
        text, re.I
    ))
    is_correction = bool(
        not has_new_links
        and (
            stash.get("awaiting_correction")
            or (update.message.reply_to_message is not None)
            or is_mode_switch_phrase
            or re.search(r"^\s*(?:исправь|переделай|поменяй|замени|перезапиши|отредактируй)\b|\bне\s+[а-яa-z0-9&_]+,\s*а\b|\bты\s+(?:много\s+что\s+)?упустил\b|\b(?:ты\s+)?(?:не\s+указал|пропустил|забыл|ошибся)\b", text, re.I)
        )
    )
    if is_correction and (stash.get("last_trade") or mode):
        await handle_trade_correction(text, update, audio_transcribed=audio_transcribed)
        return

    has_trade_keywords = re.search(
        r"(long|short|лонг|шорт|бай|селл|buy|sell|покупк|продаж|eur|usd|gbp|chf|ger|rr|\d+[rRрР]|стейк|стоп|тейк|бу|1h3m|манипуляц|fvg|фвг|босс|bos|профукал|пропустил|проспал|фунт|реал|live)",
        text,
        re.I
    )

    if not has_trade_keywords and len(stash["links"]) == 0:
        await update.message.reply_text(
            "Не удалось распознать параметры сделки. Пришли ссылки на TradingView, наговори голосом или напиши текстом (например: *EURUSD лонг 2.5R 1h3m*).",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    combined_text = text
    if stash["links"]:
        for l in stash["links"]:
            if l not in combined_text:
                combined_text += f" {l}"

    status_msg = None
    try:
        status_msg = await send_msg_with_retry(update.message.reply_text, "⏳ Формирую интерактивный черновик сделки...")
    except Exception as e:
        logger.warning(f"Could not send status_msg: {e}")

    try:
        loop = asyncio.get_running_loop()
        draft = await loop.run_in_executor(
            None,
            lambda: notion_trader.parse_trade_draft(
                combined_text,
                default_year=stash.get("year", "2026"),
                default_month=stash.get("month", "Октябрь"),
                mode=mode,
                manual_balance=stash.get("manual_balance")
            )
        )

        stash["active_draft"] = draft
        save_stash_to_disk()

        card_html = format_draft_card(draft)
        if audio_transcribed:
            card_html = f"🎙 <i>Распознано:</i> «{html.escape(text.strip())}»\n\n" + card_html

        kb = get_draft_keyboard(draft)
        if status_msg:
            try:
                await send_msg_with_retry(status_msg.edit_text, card_html, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await send_msg_with_retry(update.message.reply_text, card_html, parse_mode="HTML", reply_markup=kb)
        else:
            await send_msg_with_retry(update.message.reply_text, card_html, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        logger.exception("Error processing trade draft")
        err_msg = f"❌ Ошибка обработки: {str(e)}"
        if status_msg:
            try:
                await send_msg_with_retry(status_msg.edit_text, err_msg)
            except Exception:
                await send_msg_with_retry(update.message.reply_text, err_msg)
        else:
            await send_msg_with_retry(update.message.reply_text, err_msg)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    if not text:
        return

    if text.startswith("🔄 Режим:") or text in ["/mode"]:
        await mode_cmd(update, context)
        return
    elif text.startswith("/balance") or text.lower().startswith("баланс") or text in ["💰 Баланс", "Баланс"]:
        parts = text.split()
        if len(parts) > 1:
            context.args = parts[1:]
        else:
            context.args = []
        await balance_cmd(update, context)
        return
    elif re.match(r"^(?:режим|включи\s+режим|переключи\s+(?:на\s+)?режим)\s+(?:бэктест\w*|реал\w*|профукал\w*)$", text, re.I):
        if "реал" in text.lower():
            context.args = ["реал"]
        elif "профук" in text.lower():
            context.args = ["профукал"]
        else:
            context.args = ["бэктест"]
        await mode_cmd(update, context)
        return
    elif text in ["📊 Статистика", "/stats"]:
        await stats_cmd(update, context)
        return
    elif text in ["📑 Сделки и графики", "📑 Сделки", "/trades"]:
        await trades_cmd(update, context)
        return
    elif text in ["🌐 Web-Терминал", "/dashboard"]:
        await dashboard_cmd(update, context)
        return
    elif text in ["ℹ️ Статус месяца", "/status"]:
        await status_cmd(update, context)
        return
    elif text in ["🧹 Очистить ссылки", "/clear"]:
        await clear_cmd(update, context)
        return
    elif text.startswith("📅"):
        # Open month picker
        await month_cmd(update, context)
        return
    elif text == "➕ Как добавить сделку":
        msg = (
            "🎯 **Как добавить сделку за пару секунд:**\n\n"
            "1. В TradingView нажми `Alt+S` на **1H графике** и отправь ссылку боту.\n"
            "2. Нажми `Alt+S` на **3M графике** и отправь ссылку боту.\n"
            "3. Надиктуй **короткое аудио** или напиши текстом:\n"
            "   > *«21 сентября, фунт, лонг, 2.2R, bos 1m, франк-манипуляция, сделка в реале»*\n\n"
            "Бот автоматически сформирует интерактивный черновик со счетом и кнопками выбора базы (Реальные/Бэктест), риска и сессии!"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    await handle_trade_text(text, update, audio_transcribed=False)

async def send_msg_with_retry(msg_func, *args, **kwargs):
    for attempt in range(4):
        try:
            return await msg_func(*args, **kwargs)
        except Exception as e:
            if attempt < 3:
                await asyncio.sleep(1.0 * (attempt + 1))
            else:
                logger.error(f"Failed send_msg_with_retry after 4 attempts: {e}")
                raise e

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    user_id = update.effective_user.id
    progress_msg = None
    try:
        progress_msg = await send_msg_with_retry(update.message.reply_text, "🎧 Слушаю голосовое сообщение...")
    except Exception as e:
        logger.warning(f"Could not send progress message: {e}")

    try:
        # Download file with retries
        file = None
        for attempt in range(4):
            try:
                file = await context.bot.get_file(voice.file_id)
                break
            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(1.5)
                else:
                    raise e

        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        for attempt in range(4):
            try:
                await file.download_to_drive(tmp_path)
                break
            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(1.5)
                else:
                    raise e

        loop = asyncio.get_running_loop()
        def transcribe_audio(path):
            segments, info = whisper_model.transcribe(path, language="ru", beam_size=5)
            return " ".join(seg.text for seg in segments).strip()

        transcribed_text = await loop.run_in_executor(None, transcribe_audio, tmp_path)

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        if not transcribed_text:
            if progress_msg:
                try:
                    await send_msg_with_retry(progress_msg.edit_text, "❌ Не удалось разобрать речь. Попробуй надиктовать еще раз четче.")
                except Exception:
                    pass
            return

        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

        await handle_trade_text(transcribed_text, update, audio_transcribed=True)

    except Exception as e:
        logger.exception("Voice processing error")
        if progress_msg:
            try:
                await send_msg_with_retry(progress_msg.edit_text, f"❌ Ошибка распознавания аудио: {str(e)}")
            except Exception:
                pass
        else:
            try:
                await update.message.reply_text(f"❌ Ошибка обработки аудио: {str(e)}")
            except Exception:
                pass

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Global exception in Telegram update: {context.error}")

async def post_init(application: Application):
    commands = [
        BotCommand("mode", "Переключить: Бэктесты / Профукал / Реальные"),
        BotCommand("balance", "Баланс депозита и фаза риска"),
        BotCommand("stats", "Статистика и PnL за месяц"),
        BotCommand("trades", "Список сделок и графики TradingView"),
        BotCommand("dashboard", "Открыть Web-терминал прямо в Telegram"),
        BotCommand("status", "Статус базы и активный месяц"),
        BotCommand("month", "Выбрать месяц"),
        BotCommand("clear", "Очистить буфер ссылок"),
        BotCommand("start", "Главное меню и кнопки"),
    ]
    await application.bot.set_my_commands(commands)
    
    global TUNNEL_URL
    if not TUNNEL_URL:
        for _ in range(10):
            if TUNNEL_URL:
                break
            await asyncio.sleep(0.5)

    if TUNNEL_URL:
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Терминал", web_app=WebAppInfo(url=get_dashboard_url()))
            )
            print("✅ Кнопка Меню WebApp установлена в Telegram!")
        except Exception as e:
            print(f"Menu button notice: {e}")

    print("✅ Команды бота зарегистрированы в меню Telegram!")

def main():
    # 1. Start local web server
    threading.Thread(target=start_web_server, daemon=True).start()

    # 2. Start tunnel (Cloudflare with Pinggy fallback)
    tunnel_thread = threading.Thread(target=start_tunnel, daemon=True)
    tunnel_thread.start()

    # Wait for tunnel URL
    import time
    for _ in range(14):
        if TUNNEL_URL:
            break
        time.sleep(0.5)

    print(f"🚀 Запуск Telegram-бота @trading1812bot (WebApp: {TUNNEL_URL or 'локально'})...")
    request = HTTPXRequest(
        connect_timeout=25.0,
        read_timeout=35.0,
        write_timeout=25.0,
        pool_timeout=25.0
    )
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request).post_init(post_init).build()
    app.add_error_handler(global_error_handler)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("trades", trades_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CallbackQueryHandler(month_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))

    print("✅ Бот готов принимать сообщения и клики по кнопкам!")
    app.run_polling(drop_pending_updates=False, bootstrap_retries=-1)

if __name__ == "__main__":
    main()
