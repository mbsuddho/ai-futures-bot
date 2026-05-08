from flask import Flask
import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton
)
from binance.client import Client
from openai import OpenAI
import pandas as pd
import ta
import threading
import time
import os
from datetime import datetime

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

bot = telebot.TeleBot(TOKEN)

client = Client()

deepseek = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

app = Flask(__name__)

TIMEFRAME = Client.KLINE_INTERVAL_15MINUTE

trade_counter = 0

current_week = datetime.utcnow().isocalendar()[1]

wins = 0
losses = 0

custom_leverage = None

active_trades = {}

last_auto_signal = {}

# =========================
# KEYBOARD MENU
# =========================

def reply_menu():

    markup = ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    markup.row(
        KeyboardButton("📈 BTC Signal"),
        KeyboardButton("📈 BNB Signal")
    )

    markup.row(
        KeyboardButton("📊 Accuracy"),
        KeyboardButton("📂 Ongoing Trades")
    )

    markup.row(
        KeyboardButton("⚙️ Set Leverage")
    )

    return markup

# =========================
# START
# =========================

@bot.message_handler(commands=['start'])
def start(message):

    bot.send_message(
        message.chat.id,
        "🤖 AI Futures Bot Online",
        reply_markup=reply_menu()
    )

# =========================
# SET LEVERAGE
# =========================

@bot.message_handler(commands=['leverage'])
def set_leverage(message):

    global custom_leverage

    try:

        parts = message.text.split()

        lev = int(parts[1])

        if lev < 1 or lev > 125:

            bot.send_message(
                message.chat.id,
                "⚠️ Leverage must be between 1x and 125x."
            )

            return

        custom_leverage = f"{lev}x"

        bot.send_message(
            message.chat.id,
            f"✅ Custom leverage set to {lev}x",
            reply_markup=reply_menu()
        )

    except:

        bot.send_message(
            message.chat.id,
            "Usage:\n/leverage 10"
        )

# =========================
# KEYBOARD BUTTONS
# =========================

@bot.message_handler(func=lambda message: True)
def keyboard_buttons(message):

    global wins
    global losses

    text = message.text

    if text == "📈 BTC Signal":

        msg = analyze(
            "BTCUSDT",
            manual=True
        )

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📈 BNB Signal":

        msg = analyze(
            "BNBUSDT",
            manual=True
        )

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📊 Accuracy":

        total = wins + losses

        if total == 0:
            accuracy = 0
        else:
            accuracy = round(
                (wins / total) * 100,
                2
            )

        msg = f"""
📊 BOT ACCURACY

✅ Wins: {wins}

❌ Losses: {losses}

🎯 Accuracy: {accuracy}%
"""

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📂 Ongoing Trades":

        if len(active_trades) == 0:

            bot.send_message(
                message.chat.id,
                "⚠️ No ongoing trades.",
                reply_markup=reply_menu()
            )

            return

        text_msg = "📂 ONGOING TRADES\n\n"

        for trade_id, trade in active_trades.items():

            symbol = trade['symbol']
            signal = trade['signal']
            entry = trade['entry']

            current_price = get_current_price(symbol)

            if signal == "LONG":

                pnl = (
                    (current_price - entry)
                    / entry
                ) * 100

            else:

                pnl = (
                    (entry - current_price)
                    / entry
                ) * 100

            pnl = round(pnl, 2)

            if pnl >= 0:
                pnl_text = f"+{pnl}%"
            else:
                pnl_text = f"{pnl}%"

            text_msg += f"""
#{trade_id}

{signal} {symbol}

Entry: {round(entry,2)}

Current: {round(current_price,2)}

PNL: {pnl_text}

-------------------
"""

        bot.send_message(
            message.chat.id,
            text_msg,
            reply_markup=reply_menu()
        )

    elif text == "⚙️ Set Leverage":

        bot.send_message(
            message.chat.id,
            "Send leverage like:\n\n/leverage 5",
            reply_markup=reply_menu()
        )

# =========================
# RESET WEEKLY COUNTER
# =========================

def reset_trade_counter_if_needed():

    global trade_counter
    global current_week

    new_week = datetime.utcnow().isocalendar()[1]

    if new_week != current_week:

        trade_counter = 0

        current_week = new_week

        print("Trade counter reset.")

# =========================
# MARKET DATA
# =========================

def get_dataframe(symbol):

    klines = client.futures_klines(
        symbol=symbol,
        interval=TIMEFRAME,
        limit=200
    )

    df = pd.DataFrame(klines, columns=[
        'time','open','high','low','close','volume',
        'close_time','qav','num_trades',
        'taker_base_vol','taker_quote_vol','ignore'
    ])

    for col in [
        'open',
        'high',
        'low',
        'close',
        'volume'
    ]:
        df[col] = df[col].astype(float)

    return df

# =========================
# LIVE FUTURES PRICE
# =========================

def get_current_price(symbol):

    ticker = client.futures_symbol_ticker(
        symbol=symbol
    )

    return float(ticker['price'])

# =========================
# AI CONFIRMATION
# =========================

def ai_confirm(
    symbol,
    signal,
    confidence,
    rsi_value,
    reasons
):

    try:

        prompt = f"""
You are an expert crypto futures analyst.

Review this setup.

Symbol: {symbol}
Signal: {signal}
Confidence: {confidence}
RSI: {rsi_value}

Reasons:
{reasons}

Reject:
- sideways market
- weak momentum
- fake breakout
- low probability setup

Reply ONLY:
APPROVE
or
REJECT
"""

        response = deepseek.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1,
            max_tokens=5
        )

        result = response.choices[0].message.content.strip()

        print("AI:", result)

        return result == "APPROVE"

    except Exception as e:

        print("DeepSeek Error:", e)

        return False

# =========================
# ANALYSIS ENGINE
# =========================

def analyze(symbol, manual=False):

    global trade_counter

    reset_trade_counter_if_needed()

    df = get_dataframe(symbol)

    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    current_price = get_current_price(symbol)

    ema20 = ta.trend.ema_indicator(close, window=20)
    ema50 = ta.trend.ema_indicator(close, window=50)
    ema200 = ta.trend.ema_indicator(close, window=200)

    rsi = ta.momentum.rsi(close, window=14)

    macd = ta.trend.macd_diff(close)

    atr = ta.volatility.average_true_range(
        high,
        low,
        close,
        window=14
    )

    avg_volume = volume.rolling(20).mean()

    resistance = high.tail(20).max()

    support = low.tail(20).min()

    # sideways filter
    if abs(ema20.iloc[-1] - ema50.iloc[-1]) < current_price * 0.001:

        return f"""
⚠️ {symbol}

Market too sideways.
"""

    false_breakout = False

    if (
        high.iloc[-1] > resistance * 0.999
        and close.iloc[-1] < resistance
    ):
        false_breakout = True

    long_score = 0
    short_score = 0

    reasons = []

    if ema20.iloc[-1] > ema50.iloc[-1]:
        long_score += 20
        reasons.append("EMA Bullish")

    if ema20.iloc[-1] < ema50.iloc[-1]:
        short_score += 20

    if current_price > ema200.iloc[-1]:
        long_score += 20
        reasons.append("Above EMA200")

    if current_price < ema200.iloc[-1]:
        short_score += 20

    if 55 < rsi.iloc[-1] < 70:
        long_score += 15
        reasons.append("RSI Bullish")

    if 30 < rsi.iloc[-1] < 45:
        short_score += 15

    if macd.iloc[-1] > 0:
        long_score += 15
        reasons.append("MACD Bullish")

    if macd.iloc[-1] < 0:
        short_score += 15

    if volume.iloc[-1] > avg_volume.iloc[-1]:
        long_score += 10
        short_score += 10
        reasons.append("Strong Volume")

    if current_price > resistance:
        long_score += 10
        reasons.append("Resistance Breakout")

    if current_price < support:
        short_score += 10

    if false_breakout:
        long_score -= 25
        reasons.append("False Breakout Risk")

    required_score = 45 if manual else 70

    signal = None
    confidence = 0

    if long_score >= required_score:
        signal = "LONG"
        confidence = long_score

    elif short_score >= required_score:
        signal = "SHORT"
        confidence = short_score

    else:

        return f"""
⚠️ {symbol}

No strong setup found.
"""

    # =========================
    # AI CONFIRMATION
    # =========================

    approved = ai_confirm(
        symbol,
        signal,
        confidence,
        round(rsi.iloc[-1], 2),
        ', '.join(reasons)
    )

    if not approved:

        return f"""
⚠️ AI FILTER REJECTED

{symbol}

DeepSeek rejected this setup.
"""

    current_atr = atr.iloc[-1]

    # =========================
    # DYNAMIC TP / SL
    # =========================

    if confidence >= 90:

        tp_multiplier = 2.5
        sl_multiplier = 1.2

    elif confidence >= 80:

        tp_multiplier = 2.0
        sl_multiplier = 1.1

    elif confidence >= 70:

        tp_multiplier = 1.6
        sl_multiplier = 1.0

    else:

        tp_multiplier = 1.2
        sl_multiplier = 0.9

    if signal == "LONG":

        stop_loss = round(
            current_price - current_atr * sl_multiplier,
            2
        )

        take_profit = round(
            current_price + current_atr * tp_multiplier,
            2
        )

    else:

        stop_loss = round(
            current_price + current_atr * sl_multiplier,
            2
        )

        take_profit = round(
            current_price - current_atr * tp_multiplier,
            2
        )

    if custom_leverage:
        leverage = custom_leverage
    else:

        if confidence >= 85:
            leverage = "5x"

        elif confidence >= 70:
            leverage = "3x"

        else:
            leverage = "2x"

    trade_counter += 1

    active_trades[trade_counter] = {
        "symbol": symbol,
        "signal": signal,
        "entry": current_price,
        "tp": take_profit,
        "sl": stop_loss
    }

    msg = f"""
🚨 AI FUTURES SIGNAL

#{trade_counter}

📌 Symbol: {symbol}

📈 Signal: {signal}

💰 Entry: {round(current_price,2)}

🎯 Take Profit: {take_profit}

🛑 Stop Loss: {stop_loss}

⚡ Leverage: {leverage}

🎯 Confidence: {confidence}%

📊 RSI: {round(rsi.iloc[-1],2)}

🧠 Analysis:
{', '.join(reasons)}
"""

    return msg

# =========================
# AUTO SIGNALS
# =========================

def auto_signals():

    while True:

        try:

            for symbol in [
                "BTCUSDT",
                "BNBUSDT"
            ]:

                msg = analyze(symbol)

                if (
                    msg.startswith("⚠️")
                    or
                    "AI FILTER REJECTED" in msg
                ):
                    continue

                if last_auto_signal.get(symbol) != msg:

                    bot.send_message(
                        CHAT_ID,
                        msg,
                        reply_markup=reply_menu()
                    )

                    last_auto_signal[symbol] = msg

        except Exception as e:
            print(e)

        time.sleep(900)

# =========================
# TRADE TRACKER
# =========================

def track_trades():

    global wins
    global losses

    while True:

        try:

            remove_ids = []

            for trade_id, trade in active_trades.items():

                symbol = trade['symbol']

                signal = trade['signal']

                tp = trade['tp']

                sl = trade['sl']

                entry = trade['entry']

                current_price = get_current_price(symbol)

                if signal == "LONG":

                    pnl = (
                        (current_price - entry)
                        / entry
                    ) * 100

                else:

                    pnl = (
                        (entry - current_price)
                        / entry
                    ) * 100

                pnl = round(pnl, 2)

                # LONG
                if signal == "LONG":

                    if current_price >= tp:

                        wins += 1

                        bot.send_message(
                            CHAT_ID,
                            f"""
✅ TAKE PROFIT HIT

#{trade_id}

📌 {symbol}

📈 Signal: LONG

💰 Entry: {round(entry,2)}

🎯 TP Hit: {tp}

📊 Profit: +{pnl}%
"""
                        )

                        remove_ids.append(trade_id)

                    elif current_price <= sl:

                        losses += 1

                        bot.send_message(
                            CHAT_ID,
                            f"""
❌ STOP LOSS HIT

#{trade_id}

📌 {symbol}

📈 Signal: LONG

💰 Entry: {round(entry,2)}

🛑 SL Hit: {sl}

📊 Loss: {pnl}%
"""
                        )

                        remove_ids.append(trade_id)

                # SHORT
                else:

                    if current_price <= tp:

                        wins += 1

                        bot.send_message(
                            CHAT_ID,
                            f"""
✅ TAKE PROFIT HIT

#{trade_id}

📌 {symbol}

📉 Signal: SHORT

💰 Entry: {round(entry,2)}

🎯 TP Hit: {tp}

📊 Profit: +{pnl}%
"""
                        )

                        remove_ids.append(trade_id)

                    elif current_price >= sl:

                        losses += 1

                        bot.send_message(
                            CHAT_ID,
                            f"""
❌ STOP LOSS HIT

#{trade_id}

📌 {symbol}

📉 Signal: SHORT

💰 Entry: {round(entry,2)}

🛑 SL Hit: {sl}

📊 Loss: {pnl}%
"""
                        )

                        remove_ids.append(trade_id)

            for rid in remove_ids:
                del active_trades[rid]

        except Exception as e:

            print(e)

        time.sleep(30)

# =========================
# WEB
# =========================

@app.route('/')
def home():
    return "AI Futures Bot Running"

# =========================
# THREADS
# =========================

threading.Thread(
    target=track_trades,
    daemon=True
).start()

threading.Thread(
    target=auto_signals,
    daemon=True
).start()

threading.Thread(
    target=bot.infinity_polling,
    daemon=True
).start()

# =========================
# RUN
# =========================

if __name__ == "__main__":

    app.run(
        host='0.0.0.0',
        port=8080
    )
last_auto_signal = {}

# =========================
# KEYBOARD MENU
# =========================

def reply_menu():

    markup = ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    markup.row(
        KeyboardButton("📈 BTC Signal"),
        KeyboardButton("📈 BNB Signal")
    )

    markup.row(
        KeyboardButton("📊 Accuracy"),
        KeyboardButton("📂 Ongoing Trades")
    )

    markup.row(
        KeyboardButton("⚙️ Set Leverage")
    )

    return markup

# =========================
# START
# =========================

@bot.message_handler(commands=['start'])
def start(message):

    bot.send_message(
        message.chat.id,
        "🤖 AI Futures Bot Online",
        reply_markup=reply_menu()
    )

# =========================
# SET LEVERAGE
# =========================

@bot.message_handler(commands=['leverage'])
def set_leverage(message):

    global custom_leverage

    try:

        parts = message.text.split()

        lev = int(parts[1])

        if lev < 1 or lev > 125:

            bot.send_message(
                message.chat.id,
                "⚠️ Leverage must be between 1x and 125x."
            )

            return

        custom_leverage = f"{lev}x"

        bot.send_message(
            message.chat.id,
            f"✅ Custom leverage set to {lev}x",
            reply_markup=reply_menu()
        )

    except:

        bot.send_message(
            message.chat.id,
            "Usage:\n/leverage 10"
        )

# =========================
# KEYBOARD BUTTONS
# =========================

@bot.message_handler(func=lambda message: True)
def keyboard_buttons(message):

    global wins
    global losses

    text = message.text

    if text == "📈 BTC Signal":

        msg = analyze(
            "BTCUSDT",
            manual=True
        )

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📈 BNB Signal":

        msg = analyze(
            "BNBUSDT",
            manual=True
        )

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📊 Accuracy":

        total = wins + losses

        if total == 0:
            accuracy = 0
        else:
            accuracy = round(
                (wins / total) * 100,
                2
            )

        msg = f"""
📊 BOT ACCURACY

✅ Wins: {wins}

❌ Losses: {losses}

🎯 Accuracy: {accuracy}%
"""

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=reply_menu()
        )

    elif text == "📂 Ongoing Trades":

        if len(active_trades) == 0:

            bot.send_message(
                message.chat.id,
                "⚠️ No ongoing trades.",
                reply_markup=reply_menu()
            )

            return

        text_msg = "📂 ONGOING TRADES\n\n"

        for trade_id, trade in active_trades.items():

            symbol = trade['symbol']
            signal = trade['signal']
            entry = trade['entry']

            current_price = get_current_price(symbol)

            if signal == "LONG":

                pnl = (
                    (current_price - entry)
                    / entry
                ) * 100

            else:

                pnl = (
                    (entry - current_price)
                    / entry
                ) * 100

            pnl = round(pnl, 2)

            if pnl >= 0:
                pnl_text = f"+{pnl}%"
            else:
                pnl_text = f"{pnl}%"

            text_msg += f"""
#{trade_id}

{signal} {symbol}

Entry: {round(entry,2)}

Current: {round(current_price,2)}

PNL: {pnl_text}

-------------------
"""

        bot.send_message(
            message.chat.id,
            text_msg,
            reply_markup=reply_menu()
        )

    elif text == "⚙️ Set Leverage":

        bot.send_message(
            message.chat.id,
            "Send leverage like:\n\n/leverage 5",
            reply_markup=reply_menu()
        )

# =========================
# MARKET DATA
# =========================

def get_dataframe(symbol):

    klines = client.futures_klines(
        symbol=symbol,
        interval=TIMEFRAME,
        limit=200
    )

    df = pd.DataFrame(klines, columns=[
        'time','open','high','low','close','volume',
        'close_time','qav','num_trades',
        'taker_base_vol','taker_quote_vol','ignore'
    ])

    for col in [
        'open',
        'high',
        'low',
        'close',
        'volume'
    ]:
        df[col] = df[col].astype(float)

    return df

# =========================
# LIVE FUTURES PRICE
# =========================

def get_current_price(symbol):

    ticker = client.futures_symbol_ticker(
        symbol=symbol
    )

    return float(ticker['price'])

# =========================
# ANALYSIS ENGINE
# =========================

def analyze(symbol, manual=False):

    global trade_counter

    df = get_dataframe(symbol)

    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    current_price = get_current_price(symbol)

    ema20 = ta.trend.ema_indicator(close, window=20)
    ema50 = ta.trend.ema_indicator(close, window=50)
    ema200 = ta.trend.ema_indicator(close, window=200)

    rsi = ta.momentum.rsi(close, window=14)

    macd = ta.trend.macd_diff(close)

    atr = ta.volatility.average_true_range(
        high,
        low,
        close,
        window=14
    )

    avg_volume = volume.rolling(20).mean()

    resistance = high.tail(20).max()

    support = low.tail(20).min()

    false_breakout = False

    if (
        high.iloc[-1] > resistance * 0.999
        and close.iloc[-1] < resistance
    ):
        false_breakout = True

    long_score = 0
    short_score = 0

    reasons = []

    if ema20.iloc[-1] > ema50.iloc[-1]:
        long_score += 20
        reasons.append("EMA Bullish")

    if ema20.iloc[-1] < ema50.iloc[-1]:
        short_score += 20

    if current_price > ema200.iloc[-1]:
        long_score += 20
        reasons.append("Above EMA200")

    if current_price < ema200.iloc[-1]:
        short_score += 20

    if 55 < rsi.iloc[-1] < 70:
        long_score += 15
        reasons.append("RSI Bullish")

    if 30 < rsi.iloc[-1] < 45:
        short_score += 15

    if macd.iloc[-1] > 0:
        long_score += 15
        reasons.append("MACD Bullish")

    if macd.iloc[-1] < 0:
        short_score += 15

    if volume.iloc[-1] > avg_volume.iloc[-1]:
        long_score += 10
        short_score += 10
        reasons.append("Strong Volume")

    if current_price > resistance:
        long_score += 10
        reasons.append("Resistance Breakout")

    if current_price < support:
        short_score += 10

    if false_breakout:
        long_score -= 25
        reasons.append("False Breakout Risk")

    required_score = 45 if manual else 70

    signal = None
    confidence = 0

    if long_score >= required_score:
        signal = "LONG"
        confidence = long_score

    elif short_score >= required_score:
        signal = "SHORT"
        confidence = short_score

    else:
        return f"⚠️ {symbol}\n\nNo strong setup found."

    current_atr = atr.iloc[-1]

    if signal == "LONG":

        stop_loss = round(
            current_price - current_atr * 1.5,
            2
        )

        take_profit = round(
            current_price + current_atr * 3,
            2
        )

    else:

        stop_loss = round(
            current_price + current_atr * 1.5,
            2
        )

        take_profit = round(
            current_price - current_atr * 3,
            2
        )

    if custom_leverage:
        leverage = custom_leverage
    else:

        if confidence >= 85:
            leverage = "5x"

        elif confidence >= 70:
            leverage = "3x"

        else:
            leverage = "2x"

    trade_counter += 1

    active_trades[trade_counter] = {
        "symbol": symbol,
        "signal": signal,
        "entry": current_price,
        "tp": take_profit,
        "sl": stop_loss
    }

    msg = f"""
🚨 AI FUTURES SIGNAL

#{trade_counter}

📌 Symbol: {symbol}

📈 Signal: {signal}

💰 Entry: {round(current_price,2)}

🎯 Take Profit: {take_profit}

🛑 Stop Loss: {stop_loss}

⚡ Leverage: {leverage}

🎯 Confidence: {confidence}%

📊 RSI: {round(rsi.iloc[-1],2)}

🧠 Analysis:
{', '.join(reasons)}
"""

    return msg

# =========================
# AUTO SIGNALS
# =========================

def auto_signals():

    while True:

        try:

            for symbol in [
                "BTCUSDT",
                "BNBUSDT"
            ]:

                msg = analyze(symbol)

                if msg.startswith("⚠️"):
                    continue

                if last_auto_signal.get(symbol) != msg:

                    bot.send_message(
                        CHAT_ID,
                        msg,
                        reply_markup=reply_menu()
                    )

                    last_auto_signal[symbol] = msg

        except Exception as e:
            print(e)

        time.sleep(900)

# =========================
# TRADE TRACKER
# =========================

def track_trades():

    global wins
    global losses

    while True:

        try:

            remove_ids = []

            for trade_id, trade in active_trades.items():

                symbol = trade['symbol']

                signal = trade['signal']

                tp = trade['tp']

                sl = trade['sl']

                current_price = get_current_price(symbol)

                if signal == "LONG":

                    if current_price >= tp:
                        wins += 1
                        remove_ids.append(trade_id)

                    elif current_price <= sl:
                        losses += 1
                        remove_ids.append(trade_id)

                else:

                    if current_price <= tp:
                        wins += 1
                        remove_ids.append(trade_id)

                    elif current_price >= sl:
                        losses += 1
                        remove_ids.append(trade_id)

            for rid in remove_ids:
                del active_trades[rid]

        except Exception as e:
            print(e)

        time.sleep(60)

# =========================
# WEB
# =========================

@app.route('/')
def home():
    return "AI Futures Bot Running"

# =========================
# THREADS
# =========================

threading.Thread(
    target=track_trades,
    daemon=True
).start()

threading.Thread(
    target=auto_signals,
    daemon=True
).start()

threading.Thread(
    target=bot.infinity_polling,
    daemon=True
).start()

# =========================
# RUN
# =========================

if __name__ == "__main__":

    app.run(
        host='0.0.0.0',
        port=8080
    )
