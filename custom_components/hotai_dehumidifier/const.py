"""Constants for the Hotai Dehumidifier (unofficial) integration."""

from __future__ import annotations

DOMAIN = "hotai_dehumidifier"
VERSION = "0.1.2"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_HOST = "host"
DEFAULT_HOST = "karos.apps.exosite.io"

SIGNAL_DEVICE_UPDATE = f"{DOMAIN}_device_update"
SIGNAL_CONNECTION = f"{DOMAIN}_connection"

REFRESH_INTERVAL_SECONDS = 600

# TaiSEIA / ESH device_id -> class
ESH_DEVICE_ID_DEHUMIDIFIER = 4

# ---- ESH field keys for the dehumidifier information model (SA04 / RD* / RDI*) ----
F_POWER = "H00"  # 0 off, 1 on
F_MODE = "H01"  # see MODE_VALUES
F_TIMER_HOURS = "H02"  # 0 off, 1-99 h
F_TARGET_HUMIDITY = "H03"  # % RH
F_DEHUMIDIFY_LEVEL = "H04"  # 0-15
F_DRY_CLOTHES_LEVEL = "H05"  # 0-15
F_TEMPERATURE = "H06"  # °C (int8)
F_HUMIDITY = "H07"  # % RH
F_AUTO_SWING = "H08"  # 0/1
F_SWING_LEVEL = "H09"  # 0-15
F_TANK_FULL = "H0A"  # 0 normal, 1 full
F_FILTER_DIRTY = "H0B"  # 0 normal, 1 clean me
F_AMBIENT_LIGHT = "H0C"  # 0/1
F_AIR_PURIFY_MODE = "H0D"  # 0 off, 1-15
F_FAN_SPEED = "H0E"  # 0 auto, 1-15
F_SIDE_VENT = "H0F"  # 0 normal, 1 side vent
F_SOUND = "H10"  # 0 normal, 1 key beep, 2 tank+key
F_DEFROST = "H11"  # 0 normal, 1 defrosting
F_ERROR = "H12"  # error code
F_BODY_ANTI_MOLD = "H13"  # 0/1
F_HIGH_HUMIDITY_ALERT = "H14"  # 0/1
F_HIGH_HUMIDITY_THRESHOLD = "H15"  # % RH
F_KEY_LOCK = "H16"  # 0/1
F_REMOTE_LOCK_BITS = "H17"  # bit field
F_MUTE = "H18"  # 0 sound, 1 silent
F_CURRENT = "H19"
F_VOLTAGE = "H1A"
F_POWER_FACTOR = "H1B"
F_ACTIVE_POWER = "H1C"
F_ENERGY = "H1D"

# H01 運轉模式
MODE_VALUES: dict[int, str] = {
    0: "auto",  # 自動除濕
    1: "target",  # 設定除濕
    2: "continuous",  # 連續除濕
    3: "dry_clothes",  # 乾衣
    4: "air_purify",  # 空氣清淨
    5: "anti_mold",  # 防霉防蟎
    6: "fan_only",  # 送風
    7: "comfort",  # 人體舒適
    8: "low_humidity",  # 低濕乾燥
}
MODE_TO_VALUE: dict[str, int] = {v: k for k, v in MODE_VALUES.items()}

# H10 聲音設定
SOUND_VALUES: dict[int, str] = {0: "normal", 1: "key_beep", 2: "tank_and_key_beep"}
