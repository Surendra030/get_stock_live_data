from flask import Flask, jsonify, request
from nseconnect import Nse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pytz
import time
import math

app = Flask(__name__)

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 5
MAX_WORKERS = 30
BATCH_COUNT_NUM = 100
CAPITAL = 100000  # For calculation

nse = Nse()
stock_codes = nse.get_stock_codes()
stock_symbols = [symbol for symbol in stock_codes if symbol != "SYMBOL"]

fetched_lst = set()

# --- Utility Functions ---

# Round to nearest base value
def mround(value, base):
    return round(base * round(float(value) / base), 2)

# Risk/reward calculation
def calculate_and_save(open_price, yesterday_high, yesterday_low):
    open_price = float(open_price)
    yesterday_high = float(yesterday_high)
    yesterday_low = float(yesterday_low)

    risk_per_trade = CAPITAL * 0.01
    target_profit = CAPITAL * 0.015
    range_value = yesterday_high - yesterday_low

    buy_entry = mround((open_price + (range_value * 0.55)), 0.05)
    sell_entry = mround((open_price - (range_value * 0.55)), 0.05)

    buy_stoploss = mround(buy_entry - (buy_entry * 0.0135), 0.05)
    sell_stoploss = mround(sell_entry + (sell_entry * 0.0135), 0.05)

    risk_buy = buy_entry - buy_stoploss
    risk_sell = sell_stoploss - sell_entry

    shares_num = mround(risk_per_trade / risk_buy, 1) if risk_buy else 0

    buy_stopgain = mround(buy_entry + (target_profit / shares_num), 0.05) if shares_num else 0
    sell_stopgain = mround(sell_entry - (target_profit / shares_num), 0.05) if shares_num else 0

    return {
        "Buy_Entry": round(buy_entry, 2),
        "Sell_Entry": round(sell_entry, 2),
        "Buy_Stoploss": round(buy_stoploss, 2),
        "Sell_Stoploss": round(sell_stoploss, 2),
        "Buy_Stopgain": round(buy_stopgain, 2),
        "Sell_Stopgain": round(sell_stopgain, 2),
        "Shares_count": int(shares_num),
        "Signal": None,
        "Current_price": None,
        "Signal_Flag": None
    }

# Get list of stock symbols in batches
def get_stock_symbols():
    batch_size = max(1, len(stock_symbols) // BATCH_COUNT_NUM)
    return [stock_symbols[i:i + batch_size] for i in range(0, len(stock_symbols), batch_size)]

# Difference preserving order (though now just set difference needed)
def difference_preserve_order(lst1, lst2):
    return list(set(lst1) - set(lst2))

# Fetch NSE data for a single stock symbol
def fetch_stock_data(symbol):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            quote = nse.get_quote(symbol)
            if not quote:
                raise ValueError("Empty quote")
            return {
                'STOCK_SYMBOL': symbol,
                'STOCK_DATA': quote
            }
        except Exception as e:
            print(f"Error fetching {symbol} (Attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    return None

# --- Routes ---

@app.route("/")
def home():
    return jsonify({"message": "📈 Stock server is running!"})

@app.route("/get_all_stock_codes", methods=["GET"])
def get_all_stock_codes():
    global stock_codes, stock_symbols

    try:
        if not stock_codes:
            nse = Nse()
            stock_codes = nse.get_stock_codes()
            stock_symbols = [symbol for symbol in stock_codes if symbol != "SYMBOL"]

        batch_size = math.ceil(len(stock_symbols) / BATCH_COUNT_NUM)

        return jsonify({
            "total_stock_codes": len(stock_symbols),
            "stock_codes": stock_symbols,
            "batch_size": batch_size,
            "batch_count": math.ceil(len(stock_symbols) / batch_size)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get_stocks_data", methods=["GET"])
def get_stocks_data():
    global fetched_lst
    fetched_lst.clear()
    fetching_count = 0

    try:
        batch_param = request.args.get("batch_num", default=None, type=int)
        if batch_param is None:
            return jsonify({"error": "Please provide a valid 'batch_num' in query params"}), 400

        main_lst = get_stock_symbols()

        if batch_param < 1 or batch_param > len(main_lst):
            return jsonify({"error": f"'batch_num' must be between 1 and {len(main_lst)}"}), 400

        selected_symbols = main_lst[batch_param - 1]

        all_stock_data = []
        temp_fetched_lst = set()

        # First fetch attempt
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_stock_data, symbol): symbol for symbol in selected_symbols}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_stock_data.append(result)
                    temp_fetched_lst.add(futures[future])

        # Identify not fetched
        not_fetched_lst = list(set(selected_symbols) - temp_fetched_lst)

        # Retry if necessary
        if not_fetched_lst and fetching_count == 0:
            fetching_count += 1
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(fetch_stock_data, symbol): symbol for symbol in not_fetched_lst}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        all_stock_data.append(result)
                        temp_fetched_lst.add(futures[future])

        fetched_lst.update(temp_fetched_lst)
        selected_symbols = None if len(selected_symbols) == len(fetched_lst) else selected_symbols
        return jsonify({
            "timestamp": datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%m-%Y %H:%M"),
            "stocks": all_stock_data,
            "selected_stock": selected_symbols,
            "not_fetched_lst": list(set(selected_symbols) - fetched_lst),
            "fetched_stock": list(fetched_lst),
            "fetching_count": fetching_count
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Main Entry Point ---

if __name__ == "__main__":
    app.run(debug=True)
