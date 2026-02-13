DB_PATH = "pressure.db"

LOG_EVERY_SEC = 20
KEEP_HOURS = 24
V_FULL_SCALE = 3.3
# Enable simulation/debugging withoiut connecting sensors.
DEMO_MODE = True

# Enable/disable each sensor quickly.
PRESSURE = True
T1 = True
T2 = True
T3 = True

# You can adjust ranges to match each transmitter.
PRESSURE_NAME = "System Pressure"
PRESSURE_CHANNEL = 0
PRESSURE_UNIT = "bar"
PRESSURE_MIN = 0.0
PRESSURE_MAX = 4.0

T1_NAME = "Accumuliator Temp. Top"
T1_CHANNEL = 1
T1_UNIT = "°C"
T1_MIN = 0.0
T1_MAX = 100.0

T2_NAME = "Accumulator Temp. Bottom"
T2_CHANNEL = 2
T2_UNIT = "°C"
T2_MIN = 0.0
T2_MAX = 100.0

T3_NAME = "Outside Temp"
T3_CHANNEL = 3
T3_UNIT = "°C"
T3_MIN = 0.0
T3_MAX = 100.0

SENSORS = [
    {
        "key": "pressure",
        "enabled": PRESSURE,
        "name": PRESSURE_NAME,
        "channel": PRESSURE_CHANNEL,
        "unit": PRESSURE_UNIT,
        "value_min": PRESSURE_MIN,
        "value_max": PRESSURE_MAX,
    },
    {
        "key": "t1",
        "enabled": T1,
        "name": T1_NAME,
        "channel": T1_CHANNEL,
        "unit": T1_UNIT,
        "value_min": T1_MIN,
        "value_max": T1_MAX,
    },
    {
        "key": "t2",
        "enabled": T2,
        "name": T2_NAME,
        "channel": T2_CHANNEL,
        "unit": T2_UNIT,
        "value_min": T2_MIN,
        "value_max": T2_MAX,
    },
    {
        "key": "t3",
        "enabled": T3,
        "name": T3_NAME,
        "channel": T3_CHANNEL,
        "unit": T3_UNIT,
        "value_min": T3_MIN,
        "value_max": T3_MAX,
    },
]
