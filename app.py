import time
import threading
import sqlite3
from datetime import datetime, timedelta, timezone

import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ----------------- SETTINGS -----------------
DB_PATH = "pressure.db"

LOG_EVERY_SEC = 20              # запись в БД
KEEP_HOURS = 24                 # хранить последние 24 часа

READ_CHANNEL = 0                # A0
MAX_BAR = 5.0                   # 0-5 bar
V_FULL_SCALE = 3.3              # 20mA -> 3.3V (на твоём конвертере)
USE_CAL = False                 # если хочешь двухточечную калибровку
V0 = 0.00                       # напряжение при 0 bar (замерь)
V5 = 3.30                       # напряжение при 5 bar (замерь)
# -------------------------------------------

app = FastAPI()

def db_init():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER NOT NULL,       -- unix seconds UTC
                voltage REAL NOT NULL,
                bar REAL NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def voltage_to_bar(v: float) -> float:
    if USE_CAL:
        denom = (V5 - V0)
        if abs(denom) < 1e-9:
            return 0.0
        bar = (v - V0) / denom * MAX_BAR
    else:
        bar = (v / V_FULL_SCALE) * MAX_BAR
    return clamp(bar, 0.0, MAX_BAR)

def sensor_init():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1  # +/-4.096V range
    chan = AnalogIn(ads, getattr(ADS, f"P{READ_CHANNEL}"))
    return chan

def now_ts() -> int:
    return int(time.time())

def cleanup_old(con: sqlite3.Connection):
    cutoff = now_ts() - int(KEEP_HOURS * 3600)
    con.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))

def logger_thread():
    chan = sensor_init()
    while True:
        try:
            v = float(chan.voltage)
            bar = float(voltage_to_bar(v))
            ts = now_ts()

            with sqlite3.connect(DB_PATH) as con:
                con.execute("INSERT INTO samples(ts, voltage, bar) VALUES(?,?,?)", (ts, v, bar))
                cleanup_old(con)
                con.commit()

        except Exception as e:
            # если датчик/I2C временно отвалился — не убиваем поток
            print("LOGGER ERROR:", e)

        time.sleep(LOG_EVERY_SEC)

@app.on_event("startup")
def on_startup():
    db_init()
    t = threading.Thread(target=logger_thread, daemon=True)
    t.start()

# ---- API ----
@app.get("/api/latest")
def api_latest():
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT ts, voltage, bar FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "no data yet"})
    ts, voltage, bar = row
    return {
        "ok": True,
        "ts": ts,
        "iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "voltage": voltage,
        "bar": bar,
    }

@app.get("/api/series")
def api_series(hours: int = 24):
    hours = max(1, min(72, int(hours)))
    cutoff = now_ts() - hours * 3600
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            "SELECT ts, bar FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,)
        ).fetchall()
    # Chart.js удобно: labels + values
    labels = [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts, _ in rows]
    values = [bar for _, bar in rows]
    return {"ok": True, "labels": labels, "values": values}

# ---- Static web ----
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()
