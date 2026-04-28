import sqlite3
import threading
import time
import math
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi import Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, DEMO_MODE, KEEP_HOURS, LOG_EVERY_SEC, SENSORS, V_FULL_SCALE

app = FastAPI()
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

ENABLED_SENSORS = [sensor for sensor in SENSORS if sensor["enabled"]]
SENSOR_BY_KEY = {sensor["key"]: sensor for sensor in ENABLED_SENSORS}
TREND_VISIBILITY_KEY = "trend_visibility"

def db_init():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER NOT NULL,       -- unix seconds UTC
                sensor_key TEXT NOT NULL,
                voltage REAL NOT NULL,
                value REAL NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_samples_key_ts ON samples(sensor_key, ts)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS ui_preferences (
                pref_key TEXT PRIMARY KEY,
                pref_value TEXT NOT NULL,
                updated_ts INTEGER NOT NULL
            )
        """)

        # Migrate old schema (ts, voltage, bar) to new multi-sensor schema.
        cols = [row[1] for row in con.execute("PRAGMA table_info(samples)").fetchall()]
        if "sensor_key" not in cols:
            con.execute("ALTER TABLE samples ADD COLUMN sensor_key TEXT")
        if "value" not in cols:
            con.execute("ALTER TABLE samples ADD COLUMN value REAL")
            if "bar" in cols:
                con.execute("UPDATE samples SET value = bar WHERE value IS NULL")
        con.execute("UPDATE samples SET sensor_key = 'pressure' WHERE sensor_key IS NULL OR sensor_key = ''")
        con.commit()

def default_trend_visibility() -> dict:
    return {sensor["key"]: True for sensor in ENABLED_SENSORS}

def normalize_trend_visibility(raw) -> dict:
    allowed = {sensor["key"] for sensor in ENABLED_SENSORS}
    defaults = default_trend_visibility()
    if not isinstance(raw, dict):
        return defaults
    for key, val in raw.items():
        if key in allowed:
            defaults[key] = bool(val)
    return defaults

def get_trend_visibility() -> dict:
    defaults = default_trend_visibility()
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT pref_value FROM ui_preferences WHERE pref_key = ?",
            (TREND_VISIBILITY_KEY,),
        ).fetchone()
    if not row:
        return defaults
    try:
        parsed = json.loads(row[0])
    except Exception:
        return defaults
    return normalize_trend_visibility(parsed)

def set_trend_visibility(visibility: dict) -> dict:
    normalized = normalize_trend_visibility(visibility)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            INSERT INTO ui_preferences(pref_key, pref_value, updated_ts)
            VALUES(?, ?, ?)
            ON CONFLICT(pref_key) DO UPDATE SET
                pref_value = excluded.pref_value,
                updated_ts = excluded.updated_ts
            """,
            (TREND_VISIBILITY_KEY, json.dumps(normalized), now_ts()),
        )
        con.commit()
    return normalized

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def voltage_to_value(voltage: float, value_min: float, value_max: float) -> float:
    span = value_max - value_min
    if abs(span) < 1e-9:
        return value_min
    ratio = clamp(voltage / V_FULL_SCALE, 0.0, 1.0)
    value = value_min + (ratio * span)
    return clamp(value, value_min, value_max)

def sensor_init():
    import adafruit_ads1x15.ads1115 as ADS
    import board
    import busio
    from adafruit_ads1x15.analog_in import AnalogIn

    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1  # +/-4.096V range
    channels = {}
    for sensor in ENABLED_SENSORS:
        channel_id = sensor["channel"]
        # Use channel index directly (0-3)
        channels[sensor["key"]] = AnalogIn(ads, channel_id)
    return channels

def now_ts() -> int:
    return int(time.time())

def cleanup_old(con: sqlite3.Connection):
    cutoff = now_ts() - int(KEEP_HOURS * 3600)
    con.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))

def demo_sensor_values(ts: int) -> dict:
    # Outside temperature: day/night sinusoid (24h period, sign switch ~every 12h).
    day_phase = (2.0 * math.pi * (ts % 86400)) / 86400.0
    outside = clamp(10.0 * math.sin(day_phase - (math.pi / 2.0)), -10.0, 10.0)

    # Tank temperatures: proportional heating/cooling cycle every ~3.5 hours.
    tank_period = 3.5 * 3600.0
    tank_phase = (2.0 * math.pi * (ts % tank_period)) / tank_period
    tank_wave = (math.sin(tank_phase - (math.pi / 2.0)) + 1.0) / 2.0  # 0..1

    # Slight day bias so daytime is marginally warmer overall.
    day_bias = (outside / 10.0) * 0.05
    top_ratio = clamp(tank_wave + day_bias, 0.0, 1.0)
    bottom_ratio = clamp((tank_wave * 0.78) + 0.10 + (day_bias * 0.6), 0.0, 1.0)

    top = 20.0 + (70.0 * top_ratio)       # 20..90
    bottom = 20.0 + (45.0 * bottom_ratio) # 20..65
    bottom = min(bottom, top - 2.0)
    bottom = clamp(bottom, 20.0, 65.0)

    # Pressure follows top temperature (hotter top -> higher pressure).
    top_norm = clamp((top - 20.0) / 70.0, 0.0, 1.0)
    pressure_wave = 0.03 * math.sin(tank_phase + 0.4)
    pressure = clamp(1.0 + top_norm + pressure_wave, 1.0, 2.0)

    return {
        "pressure": pressure,
        "t1": top,
        "t2": bottom,
        "t3": outside,
    }

def logger_thread():
    channels = None
    use_demo = DEMO_MODE
    
    if not DEMO_MODE:
        try:
            channels = sensor_init()
            print(f"[LOGGER] Sensors initialized: {list(channels.keys())}")
            use_demo = False
        except Exception as e:
            print(f"[LOGGER] ERROR initializing sensors: {e}")
            print(f"[LOGGER] Falling back to DEMO_MODE")
            use_demo = True
    
    while True:
        try:
            ts = now_ts()
            rows = []
            simulated = demo_sensor_values(ts) if use_demo else {}
            for idx, sensor in enumerate(ENABLED_SENSORS):
                key = sensor["key"]
                if use_demo or channels is None:
                    value_min = sensor["value_min"]
                    value_max = sensor["value_max"]
                    span = value_max - value_min
                    raw_value = simulated.get(key)
                    if raw_value is None:
                        midpoint = value_min + (span / 2.0)
                        amplitude = span * 0.35
                        phase = ((ts % 300) / 300.0) * (2.0 * math.pi) + idx
                        raw_value = midpoint + amplitude * math.sin(phase)
                    value = clamp(raw_value, value_min, value_max)
                    ratio = (value - value_min) / span if abs(span) > 1e-9 else 0.0
                    voltage = clamp(ratio * V_FULL_SCALE, 0.0, V_FULL_SCALE)
                else:
                    voltage = float(channels[key].voltage)
                    value = float(voltage_to_value(voltage, sensor["value_min"], sensor["value_max"]))
                rows.append((ts, key, voltage, value))

            with sqlite3.connect(DB_PATH) as con:
                con.executemany(
                    "INSERT INTO samples(ts, sensor_key, voltage, value) VALUES(?,?,?,?)",
                    rows,
                )
                cleanup_old(con)
                con.commit()

        except Exception as e:
            # если датчик/I2C временно отвалился — не убиваем поток
            print("LOGGER ERROR:", e)

        time.sleep(LOG_EVERY_SEC)

@app.on_event("startup")
def on_startup():
    db_init()
    if ENABLED_SENSORS:
        t = threading.Thread(target=logger_thread, daemon=True)
        t.start()

# ---- API ----
@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "server_now_ts": now_ts(),
        "server_now_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trend_visibility": get_trend_visibility(),
        "log_every_sec": LOG_EVERY_SEC,
        "sensors": [
            {
                "key": sensor["key"],
                "name": sensor["name"],
                "unit": sensor["unit"],
                "channel": sensor["channel"],
                "value_min": sensor["value_min"],
                "value_max": sensor["value_max"],
            }
            for sensor in ENABLED_SENSORS
        ],
    }

@app.get("/api/latest")
def api_latest():
    if not ENABLED_SENSORS:
        return JSONResponse({"ok": False, "error": "all sensors disabled in config"})

    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            """
            SELECT s.sensor_key, s.ts, s.voltage, s.value
            FROM samples s
            INNER JOIN (
                SELECT sensor_key, MAX(ts) AS max_ts
                FROM samples
                GROUP BY sensor_key
            ) latest
            ON s.sensor_key = latest.sensor_key AND s.ts = latest.max_ts
            """
        ).fetchall()

    if not rows:
        return JSONResponse({"ok": False, "error": "no data yet"})

    payload = []
    for sensor_key, ts, voltage, value in rows:
        sensor = SENSOR_BY_KEY.get(sensor_key)
        if not sensor:
            continue
        payload.append(
            {
                "key": sensor_key,
                "name": sensor["name"],
                "unit": sensor["unit"],
                "channel": sensor["channel"],
                "ts": ts,
                "iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "local": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "voltage": voltage,
                "value": value,
            }
        )

    payload.sort(key=lambda row: row["name"])
    return {
        "ok": True,
        "server_now_ts": now_ts(),
        "server_now_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sensors": payload,
    }

@app.get("/api/series")
def api_series(hours: int = 24):
    if not ENABLED_SENSORS:
        return JSONResponse({"ok": False, "error": "all sensors disabled in config"})

    hours = max(1, min(168, int(hours)))
    cutoff = now_ts() - hours * 3600
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            "SELECT ts, sensor_key, value FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,)
        ).fetchall()

    unique_ts = []
    seen = set()
    for ts, _, _ in rows:
        if ts not in seen:
            unique_ts.append(ts)
            seen.add(ts)

    labels = [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in unique_ts]
    ts_index = {ts: idx for idx, ts in enumerate(unique_ts)}

    datasets = {
        sensor["key"]: {
            "key": sensor["key"],
            "name": sensor["name"],
            "unit": sensor["unit"],
            "value_min": sensor["value_min"],
            "value_max": sensor["value_max"],
            "values": [None] * len(unique_ts),
        }
        for sensor in ENABLED_SENSORS
    }

    for ts, sensor_key, value in rows:
        if sensor_key in datasets:
            datasets[sensor_key]["values"][ts_index[ts]] = value

    return {
        "ok": True,
        "server_now_ts": now_ts(),
        "server_now_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trend_visibility": get_trend_visibility(),
        "labels": labels,
        "datasets": list(datasets.values()),
    }

@app.get("/api/trend_visibility")
def api_trend_visibility_get():
    return {
        "ok": True,
        "server_now_ts": now_ts(),
        "server_now_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trend_visibility": get_trend_visibility(),
    }

@app.post("/api/trend_visibility")
def api_trend_visibility_set(payload: dict = Body(...)):
    visibility = payload.get("trend_visibility", {})
    saved = set_trend_visibility(visibility)
    return {
        "ok": True,
        "server_now_ts": now_ts(),
        "server_now_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trend_visibility": saved,
    }

# ---- Static web ----
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def render_index(lang: str) -> str:
    lang = "ru" if lang == "ru" else "en"
    with open(STATIC_DIR / "index.html", "r", encoding="utf-8") as f:
        template = f.read()
    return template.replace("__LANG__", lang)

@app.get("/")
def root():
    return RedirectResponse(url="/en", status_code=307)

@app.get("/en", response_class=HTMLResponse)
def index_en():
    return render_index("en")

@app.get("/ru", response_class=HTMLResponse)
def index_ru():
    return render_index("ru")
