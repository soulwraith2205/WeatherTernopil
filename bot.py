import json
import os
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Координати Тернополя
LAT = 49.5535
LON = 25.5948
KYIV_TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = "state.json"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

# Налаштування шапок та текстів для кожного типу погоди
WEATHER_TEMPLATES = {
    "thunderstorm": {
        "header": "⚡ УВАГА! ГРОЗА В ТЕРНОПОЛІ",
        "text": "Наближається гроза 🌩️ Можливі грозові розряди та посилення вітру."
    },
    "hail": {
        "header": "🧊 ОБЕРЕЖНО! ОЧІКУЄТЬСЯ ГРАД",
        "text": "Наближається гроза з градом ⛈️ Подбайте про укриття та захист авто."
    },
    "freezing_rain": {
        "header": "❄️💧 УВАГА! КРИЖАНИЙ ДОЩ",
        "text": "Наближається крижаний дощ 🧊 На дорогах і тротуарах можлива сильна ожеледь."
    },
    "ice_pellets": {
        "header": "🧊💧 ОПАДИ: ЛЬОДЯНА КРУПА",
        "text": "Наближається льодяна крупа / крижана мряка ❄️ Будьте обережні на дорогах."
    },
    "black_ice": {
        "header": "🧊⚠️ УВАГА! ОЖЕЛЕДИЦЯ НА ДОРОГАХ",
        "text": "Очікується утворення ожеледиці ❄️ На дорогах і тротуарах буде дуже слизько — будьте обережні за кермом та під час ходьби."
    },
    "blizzard": {
        "header": "🌨️💨 УВАГА! ХУРТОВИНА",
        "text": "Наближається хуртовина ❄️ Снігопад супроводжуватиметься сильними поривами вітру."
    },
    "snow": {
        "header": "❄️ ОПАДИ В МІСТІ ТЕРНОПІЛЬ: СНІГ",
        "text": "Наближається сніг 🌨️ Очікується снігопад."
    },
    "snow_grains": {
        "header": "🌨️ ОПАДИ: СНІГОВА КРУПА",
        "text": "Наближається снігова крупа (снігові зерна) ❄️"
    },
    "downpour": {
        "header": "⛈️ УВАГА! СИЛЬНА ЗЛИВА",
        "text": "Наближається інтенсивна злива 🌧️ Візьміть парасольку та врахуйте можливе підтоплення доріг."
    },
    "rain": {
        "header": "🌧️ ОПАДИ В МІСТІ ТЕРНОПІЛЬ: ДОЩ",
        "text": "Наближається дощ 💧 Не забудьте парасольку."
    },
    "drizzle": {
        "header": "🌦️ ОПАДИ В МІСТІ ТЕРНОПІЛЬ: МРЯКА",
        "text": "Наближається дрібна мряка 🌫️ Підвищена вологість."
    },
    "fog": {
        "header": "🌫️ УВАГА! ТУМАН У ТЕРНОПОЛІ",
        "text": "Очікується туман 👀 Видимість на дорогах може суттєво знизитися."
    },
    "wind": {
        "header": "💨 УВАГА! СИЛЬНІ ПОРИВИ ВІТРУ",
        "text": "Очікуються сильні пориви вітру 🌬️ Уникайте перебування під старими деревами та білбордами."
    },
    "extreme_heat": {
        "header": "🥵🔥 УВАГА! СИЛЬНА СПЕКА В ТЕРНОПОЛІ",
        "text": "Очікується сильна спека ☀️ Пийте більше води, уникайте тривалого перебування на сонці та не залишайте дітей чи тварин у зачиненому авто."
    }
}

WEEKDAYS_UA = {
    0: "у понеділок",
    1: "у вівторок",
    2: "у середу",
    3: "у четвер",
    4: "у п'ятницю",
    5: "у суботу",
    6: "у неділю",
}

WET_WEATHER_CODES = {
    51, 53, 55, 56, 57,
    61, 63, 65, 66, 67,
    71, 73, 75, 77,
    80, 81, 82, 85, 86
}


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def format_approx_time(event_dt: datetime, now_dt: datetime) -> str:
    today = now_dt.date()
    event_date = event_dt.date()

    if event_date == today:
        day_str = "сьогодні"
    elif event_date == today + timedelta(days=1):
        day_str = "завтра"
    else:
        day_str = WEEKDAYS_UA[event_dt.weekday()]

    hour = event_dt.hour
    if 0 <= hour < 6:
        part_of_day = "вночі"
    elif 6 <= hour < 12:
        part_of_day = "вранці"
    elif 12 <= hour < 18:
        part_of_day = "вдень"
    else:
        part_of_day = "ввечері"

    return f"{day_str} {part_of_day} (близько {event_dt.strftime('%H:%M')})"


def format_temp(temp: float) -> str:
    t = round(temp)
    return f"+{t}°C" if t > 0 else f"{t}°C"


def classify_weather(
    code: int,
    gusts_ms: float,
    prob: int | None,
    temp: float | None,
    is_wet: bool,
    had_recent_moisture: bool
) -> list[str]:
    events = []
    has_precip_chance = prob is None or prob >= 50

    if has_precip_chance:
        if code in (96, 99):
            events.append("hail")
        elif code == 95:
            events.append("thunderstorm")
        elif code in (66, 67):
            events.append("freezing_rain")
        elif code in (56, 57):
            events.append("ice_pellets")
        elif code in (73, 75, 85, 86) and gusts_ms >= 14.0:
            events.append("blizzard")
        elif code in (71, 73, 75, 85, 86):
            events.append("snow")
        elif code == 77:
            events.append("snow_grains")
        elif code in (65, 82):
            events.append("downpour")
        elif code in (61, 63, 80, 81):
            events.append("rain")
        elif code in (51, 53, 55):
            events.append("drizzle")

    if code in (45, 48):
        events.append("fog")

    # Сильні пориви вітру від 15 м/с
    if gusts_ms >= 15.0:
        events.append("wind")

    # Ожеледиця: переохолоджені опади або температура <= 0°C на вологому покритті
    if temp is not None and temp <= 0.0:
        if code in (56, 57, 66, 67) or (has_precip_chance and is_wet) or had_recent_moisture:
            events.append("black_ice")

    # Сильна спека: температура від +30°C
    if temp is not None and temp >= 30.0:
        events.append("extreme_heat")

    return events


def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def main() -> None:
    now_kyiv = datetime.now(KYIV_TZ)
    today_str = now_kyiv.strftime("%Y-%m-%d")
    state = load_state()

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={LAT}&longitude={LON}"
        f"&hourly=weather_code,precipitation_probability,wind_gusts_10m,temperature_2m,precipitation"
        f"&wind_speed_unit=ms&timezone=Europe%2FKyiv&forecast_days=3"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    hourly = data["hourly"]
    times = hourly["time"]
    codes = hourly["weather_code"]
    probs = hourly["precipitation_probability"]
    gusts = hourly["wind_gusts_10m"]
    temps = hourly["temperature_2m"]
    precips = hourly["precipitation"]

    detected_events = {}
    horizon_24h = now_kyiv + timedelta(hours=24)
    horizon_48h = now_kyiv + timedelta(hours=48)
    last_wet_dt = None

    for t_str, code, prob, gust, temp, precip in zip(times, codes, probs, gusts, temps, precips):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=KYIV_TZ)

        is_wet = (precip is not None and precip > 0.1) or (code in WET_WEATHER_CODES)
        had_recent_moisture = (
            last_wet_dt is not None and (dt - last_wet_dt) <= timedelta(hours=6)
        )
        if is_wet:
            last_wet_dt = dt

        if dt <= now_kyiv or dt > horizon_48h:
            continue

        matched = classify_weather(
            code,
            gust or 0.0,
            prob,
            temp,
            is_wet,
            had_recent_moisture
        )

        for event_key in matched:
            # Для дощу горизонт перевірки 48 годин, для інших явищ — 24 години
            max_horizon = horizon_48h if event_key in ("rain", "downpour") else horizon_24h
            if dt > max_horizon:
                continue

            if event_key not in detected_events:
                detected_events[event_key] = {
                    "dt": dt,
                    "prob": prob,
                    "gust": gust,
                    "temp": temp
                }
            elif (
                event_key == "extreme_heat"
                and temp is not None
                and dt.date() == detected_events[event_key]["dt"].date()
            ):
                # Фіксуємо максимальну температуру спеки протягом того ж дня
                current_max = detected_events[event_key]["temp"]
                if current_max is None or temp > current_max:
                    detected_events[event_key]["temp"] = temp

    state_changed = False

    for event_key, info in detected_events.items():
        if state.get(event_key) == today_str:
            continue

        tpl = WEATHER_TEMPLATES[event_key]
        when_str = format_approx_time(info["dt"], now_kyiv)

        details = f"🕒 <b>Орієнтовний час:</b> {when_str}"
        if event_key == "wind" and info["gust"] is not None:
            details += f"\n🌬️ <b>Пориви вітру:</b> до {round(info['gust'])} м/с"
        elif event_key == "extreme_heat" and info["temp"] is not None:
            details += f"\n🌡️ <b>Температура:</b> до {format_temp(info['temp'])}"
        elif event_key == "black_ice" and info["temp"] is not None:
            details += f"\n🌡️ <b>Температура повітря:</b> {format_temp(info['temp'])}"
        elif event_key != "fog" and info["prob"] is not None:
            details += f"\n📊 <b>Імовірність:</b> {info['prob']}%"

        message = f"<b>{tpl['header']}</b>\n\n{tpl['text']}\n\n{details}"

        send_telegram_message(message)
        state[event_key] = today_str
        state_changed = True

    if state_changed:
        save_state(state)


if __name__ == "__main__":
    main()
