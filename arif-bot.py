import os
import time
import hmac
import hashlib
import requests
import csv
from datetime import datetime

# ========================================
# CONFIGURASI GRID BOT
# ========================================
GRID_LOW = 1835000000      # Batas bawah harga BTC
GRID_HIGH = 1845000000     # Batas atas harga BTC
GRID_STEP = 50000          # Jarak antar grid (50 ribu)
TOLERANCE = 50000          # Toleransi harga (50 ribu)
TRADE_AMOUNT = 0.00001     # Jumlah BTC tiap transaksi
SLEEP_TIME = 2             # Jeda pengecekan harga (detik)

# ========================================
# MODE BOT
# ========================================
# mode = "SIMULASI" atau mode = "LIVE"
MODE = "SIMULASI"

# ========================================
# SALDO AWAL (untuk simulasi)
# ========================================
START_BALANCE_IDR = 500000     # Modal awal dalam Rupiah
START_BALANCE_BTC = 0.0000     # Modal awal BTC

# ========================================
# KONFIGURASI API (Hanya jika MODE = LIVE)
# ========================================
API_KEY = "ISI_API_KEY"
API_SECRET = "ISI_API_SECRET"
API_URL = "https://indodax.com/api/"

# ========================================
# FILE LOG
# ========================================
LOG_FILE = "trade_log.csv"

# ========================================
# VARIABEL GLOBAL
# ========================================
saldo_idr = START_BALANCE_IDR
saldo_btc = START_BALANCE_BTC
open_positions = []  # Contoh: [{'buy_price': 1835000000, 'amount': 0.0001}]

# ========================================
# FUNGSI: Ambil harga terbaru BTC
# ========================================
def get_current_price():
    if MODE == "LIVE":
        try:
            response = requests.get(API_URL + "ticker/btc_idr")
            data = response.json()
            return int(data['ticker']['last'])
        except Exception as e:
            print(f"[ERROR] Gagal ambil harga: {e}")
            return None
    else:
        # SIMULASI: harga acak bergerak naik turun
        import random
        return random.randint(GRID_LOW - 100000, GRID_HIGH + 100000)

# ========================================
# FUNGSI: Buat grid
# ========================================
def generate_grid():
    return [price for price in range(GRID_LOW, GRID_HIGH + GRID_STEP, GRID_STEP)]

# ========================================
# FUNGSI: BUY
# ========================================
def execute_buy(price):
    global saldo_idr, saldo_btc

    total_cost = price * TRADE_AMOUNT
    if saldo_idr >= total_cost:
        saldo_idr -= total_cost
        saldo_btc += TRADE_AMOUNT
        open_positions.append({'buy_price': price, 'amount': TRADE_AMOUNT})
        print(f"🟢 BUY @ {price:,} | Beli {TRADE_AMOUNT} BTC | Sisa IDR: {saldo_idr:,.2f}")
        log_transaction("BUY", price, TRADE_AMOUNT, saldo_idr, saldo_btc)
    else:
        print("[DEBUG] BUY gagal: saldo IDR kurang")

# ========================================
# FUNGSI: SELL
# ========================================
def execute_sell(price):
    global saldo_idr, saldo_btc

    if not open_positions:
        print("[DEBUG] SELL gagal: tidak ada BTC untuk dijual")
        return

    posisi = open_positions.pop(0)
    revenue = price * posisi['amount']
    saldo_idr += revenue
    saldo_btc -= posisi['amount']

    profit = price - posisi['buy_price']
    print(f"🔴 SELL @ {price:,} | Jual {posisi['amount']} BTC | Profit per BTC: {profit:,.0f}")
    log_transaction("SELL", price, posisi['amount'], saldo_idr, saldo_btc)

# ========================================
# FUNGSI: LOG TRANSAKSI
# ========================================
def log_transaction(action, price, amount, idr_balance, btc_balance):
    with open(LOG_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([datetime.now(), action, price, amount, idr_balance, btc_balance])

# ========================================
# MAIN LOOP
# ========================================
def main():
    global saldo_idr, saldo_btc
    print("=== MEMULAI GRID BOT ===")
    print(f"MODE: {MODE}")
    print(f"Saldo awal: {saldo_idr:,.0f} IDR | {saldo_btc:.8f} BTC\n")

    grid = generate_grid()

    # Buat header file log
    with open(LOG_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Waktu", "Aksi", "Harga", "Jumlah", "Saldo IDR", "Saldo BTC"])

    while True:
        price = get_current_price()
        if price is None:
            time.sleep(SLEEP_TIME)
            continue

        print(f"\n[INFO] Harga saat ini: {price:,} | IDR: {saldo_idr:,.2f} | BTC: {saldo_btc:.8f}")

        closest_grid = min(grid, key=lambda x: abs(x - price))
        print(f"📊 Harga dekat grid {closest_grid:,}")

        # BUY jika harga turun ke grid
        if price <= closest_grid + TOLERANCE and saldo_idr >= price * TRADE_AMOUNT:
            execute_buy(price)

        # SELL jika harga naik dari grid
        if price >= closest_grid - TOLERANCE and saldo_btc >= TRADE_AMOUNT and open_positions:
            last_buy = open_positions[0]['buy_price']
            if price > last_buy:  # pastikan profit
                execute_sell(price)
            else:
                print(f"[DEBUG] BELUM profit: Target {last_buy + 50000:,}, Harga sekarang {price:,}")

        time.sleep(SLEEP_TIME)

# ========================================
# JALANKAN BOT
# ========================================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot dihentikan oleh user.")
