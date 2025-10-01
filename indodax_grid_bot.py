
import os
import time
import hmac
import hashlib
import requests
import csv
import random
from datetime import datetime

# ----------------- CONFIG (you can edit or use config.json later) -----------------
MODE = os.environ.get("MODE", "SIMULATION").upper()  # SIMULATION | HYBRID | LIVE
PAIR = os.environ.get("PAIR", "btc_idr")             # Indodax pair
TOLERANCE = int(os.environ.get("TOLERANCE", "50000"))    # 50k IDR tolerance
GRID_PERCENT = float(os.environ.get("GRID_PERCENT", "0.02"))  # ±2%
GRID_STEP = int(os.environ.get("GRID_STEP", "50000"))    # 50k step
SLEEP = float(os.environ.get("SLEEP", "1"))            # seconds per loop
LOG_FILE = os.environ.get("LOG_FILE", "trade_log.csv")
VOLATILITY = int(os.environ.get("VOLATILITY", "2000000"))  # used for SIMULATION price walk
MAX_IDR_PER_ORDER = int(os.environ.get("MAX_IDR_PER_ORDER", "500000"))  # cap per order

# Stop loss / TP for last buy (optional simple)
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "0.05"))   # 5%
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "0.03")) # 3%

# ----------------- Read API keys from .env if present -----------------
def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k,v = line.split("=",1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env(".env")
API_KEY = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

# ----------------- Helpers -----------------
def ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def human(n):
    try:
        return f"{n:,.0f}"
    except:
        return str(n)

def sign(payload, secret):
    msg = "&".join([f"{k}={v}" for k,v in payload.items()])
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha512).hexdigest()

# ----------------- Indodax API funcs -----------------
TAPI = "https://indodax.com/tapi"
TICKER = "https://indodax.com/api/ticker/{}"

def tapi_request(method, extra=None, timeout=10):
    if not extra:
        extra = {}
    params = {"method": method, "timestamp": int(time.time())}
    params.update(extra)
    signv = sign(params, API_SECRET)
    headers = {"Key": API_KEY, "Sign": signv}
    try:
        r = requests.post(TAPI, headers=headers, data=params, timeout=timeout)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_price(pair=PAIR):
    try:
        r = requests.get(TICKER.format(pair), timeout=6)
        data = r.json()
        last = data.get("ticker", {}).get("last")
        if last is None:
            return None
        return int(float(last))
    except Exception:
        return None

def get_balance_live():
    res = tapi_request("getInfo")
    if not isinstance(res, dict):
        return 0.0, 0.0, res
    if "return" in res and "balance" in res["return"]:
        bal = res["return"]["balance"]
        try:
            idr = float(bal.get("idr", 0) or 0)
            btc = float(bal.get("btc", 0) or 0)
            return idr, btc, res
        except:
            return 0.0,0.0,res
    return 0.0,0.0,res

def place_buy_live(price_int, idr_amount):
    params = {"pair": PAIR, "type": "buy", "price": str(int(price_int)), "idr": str(int(idr_amount))}
    return tapi_request("trade", params)

def place_sell_live(price_int, btc_amount):
    params = {"pair": PAIR, "type": "sell", "price": str(int(price_int)), "btc": str(btc_amount)}
    return tapi_request("trade", params)

# ----------------- Simulation price engine -----------------
def simulate_price(prev, vol=VOLATILITY):
    return max(1, int(prev + random.randint(-vol, vol)))

# ----------------- Logging -----------------
def init_log(path=LOG_FILE):
    first = not os.path.exists(path)
    f = open(path, "a", newline="")
    writer = csv.writer(f)
    if first:
        writer.writerow(["timestamp_utc","mode","action","price","amount_btc","balance_idr","balance_btc","note"])
    return f, writer

# ----------------- Adaptive trade sizing -----------------
def pick_trade_amount(idr_balance):
    # rules:
    # <500k -> 0.00001
    # 500k-5M -> 0.00005
    # 5M-50M -> 0.0001
    # >50M -> 0.001
    if idr_balance < 500_000:
        return 0.00001
    if idr_balance < 5_000_000:
        return 0.00005
    if idr_balance < 50_000_000:
        return 0.0001
    return 0.001

# ----------------- Main bot -----------------
def build_grid_around(price):
    low = int(price * (1 - GRID_PERCENT))
    high = int(price * (1 + GRID_PERCENT))
    # align low/high to GRID_STEP
    low = (low // GRID_STEP) * GRID_STEP
    high = (high // GRID_STEP) * GRID_STEP
    if high <= low:
        high = low + GRID_STEP * 10
    levels = [p for p in range(low, high + GRID_STEP, GRID_STEP)]
    return levels

def main():
    print("Indodax Grid Bot — adaptive (small -> large)")
    print("MODE:", MODE)
    log_f, log_writer = init_log(LOG_FILE)

    # starting price
    if MODE == "SIMULATION":
        price = int(os.environ.get("START_PRICE", "925000000"))
    else:
        # attempt get live price; fallback to default
        lp = get_live_price()
        price = lp if lp and lp>0 else int(os.environ.get("START_PRICE", "925000000"))

    grid_levels = build_grid_around(price)
    print(f"Initial price {price:,} -> grid {grid_levels[0]:,} .. {grid_levels[-1]:,} step {GRID_STEP:,}")

    # balances (local simulation)
    balance_idr = float(os.environ.get("INITIAL_IDR", "5000000"))
    balance_btc = float(os.environ.get("INITIAL_BTC", "0"))

    last_buy_price = None
    tick = 0
    try:
        while True:
            tick += 1
            # fetch price
            if MODE == "SIMULATION":
                price = simulate_price(price)
            else:
                live = get_live_price()
                if live:
                    price = live
                else:
                    price = simulate_price(price // 1000) * 1000  # fallback small walk

            # dynamic rebuild grid every N ticks or if USE env set
            if tick % 30 == 0:
                grid_levels = build_grid_around(price)

            # if LIVE mode, fetch balances from API (overwrite local)
            if MODE == "LIVE":
                idr_bal, btc_bal, raw = get_balance_live()
                balance_idr, balance_btc = float(idr_bal), float(btc_bal)

            # compute portfolio value
            pv = balance_idr + balance_btc * price
            print(f"[{ts()}] Tick {tick} | Price {price:,} | IDR {human(balance_idr)} | BTC {balance_btc:.6f} | PV {human(pv)}")

            # emergency checks (very conservative)
            if pv <= 0:
                print("Zero portfolio, stopping.")
                break

            # pick closest grid
            closest = min(grid_levels, key=lambda x: abs(x - price))
            print(" -> closest grid:", f"{closest:,}")

            # determine adaptive trade amount based on available IDR
            adaptive_amount = pick_trade_amount(balance_idr)
            # ensure not exceed max idr per order
            expected_cost = price * adaptive_amount
            if expected_cost > MAX_IDR_PER_ORDER:
                adaptive_amount = MAX_IDR_PER_ORDER / price

            # BUY condition: price <= closest + TOLERANCE and have IDR for at least minimal cost
            min_cost_needed = price * adaptive_amount
            if price <= closest + TOLERANCE and balance_idr >= min_cost_needed and adaptive_amount > 0:
                note = ""
                if MODE == "LIVE":
                    # convert to int idr amount to send
                    idr_to_spend = min(int(min_cost_needed), MAX_IDR_PER_ORDER)
                    res = place_buy_live(price, idr_to_spend)
                    note = f"live_res={res}"
                else:
                    # simulated buy
                    cost = price * adaptive_amount
                    balance_idr -= cost
                    balance_btc += adaptive_amount
                    last_buy_price = price
                    note = "sim_buy"
                print(f" [BUY] executed amount={adaptive_amount:.8f} | cost={int(price*adaptive_amount):,} | note={note}")
                log_writer.writerow([ts(), MODE, "BUY", price, adaptive_amount, balance_idr, balance_btc, note])
                log_f.flush()

            # SELL condition: price >= closest - TOLERANCE and have BTC
            elif price >= closest - TOLERANCE and balance_btc >= adaptive_amount and adaptive_amount > 0:
                note = ""
                sell_amount = min(adaptive_amount, balance_btc)
                if MODE == "LIVE":
                    res = place_sell_live(price, sell_amount)
                    note = f"live_res={res}"
                else:
                    revenue = price * sell_amount
                    balance_btc -= sell_amount
                    balance_idr += revenue
                    last_buy_price = None
                    note = "sim_sell"
                print(f" [SELL] executed amount={sell_amount:.8f} | revenue={int(price*sell_amount):,} | note={note}")
                log_writer.writerow([ts(), MODE, "SELL", price, sell_amount, balance_idr, balance_btc, note])
                log_f.flush()

            # Check simple SL/TP on last buy
            if last_buy_price:
                slp = last_buy_price * (1 - STOP_LOSS_PCT)
                tpp = last_buy_price * (1 + TAKE_PROFIT_PCT)
                if price <= slp and balance_btc>0:
                    # sell all to cut loss
                    amt = balance_btc
                    if MODE == "LIVE":
                        res = place_sell_live(price, amt)
                        note = f"live_res={res}"
                    else:
                        balance_idr += price * amt
                        balance_btc = 0.0
                        note = "sim_sl_sell"
                    print(f" [SL] triggered. Sold all {amt:.8f} BTC @ {price:,} note={note}")
                    log_writer.writerow([ts(), MODE, "STOP_LOSS_SELL", price, amt, balance_idr, balance_btc, note])
                    log_f.flush()
                    last_buy_price = None
                elif price >= tpp and balance_btc>0:
                    amt = balance_btc
                    if MODE == "LIVE":
                        res = place_sell_live(price, amt)
                        note = f"live_res={res}"
                    else:
                        balance_idr += price * amt
                        balance_btc = 0.0
                        note = "sim_tp_sell"
                    print(f" [TP] triggered. Sold all {amt:.8f} BTC @ {price:,} note={note}")
                    log_writer.writerow([ts(), MODE, "TAKE_PROFIT_SELL", price, amt, balance_idr, balance_btc, note])
                    log_f.flush()
                    last_buy_price = None

            time.sleep(SLEEP)
    except KeyboardInterrupt:
        print("\nExiting (KeyboardInterrupt).")
    finally:
        try:
            log_f.close()
        except:
            pass

if __name__ == "__main__":
    main()
