import json
import os
import socket
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# Примусово використовуємо IPv4 та зменшений розмір TCP-пакетів (MSS 1200)
_orig_create_connection = socket.create_connection


def _small_mss_create_connection(
    address,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address=None,
    *,
    all_errors=False
):
    host, port = address
    err = None
    for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1200)
            except OSError:
                pass
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    raise OSError("getaddrinfo returns an empty list")


socket.create_connection = _small_mss_create_connection

# Координати Тернополя
LAT = 49.5535
LON = 25.5948
KYIV_TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = "state.json"

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
raw_channel_id = os.environ["CHANNEL_ID"].strip()
CHANNEL_ID = (
    raw_channel_id
    if raw_channel_id.startswith(("@", "-"))
    else f"@{raw_channel_id}"
)

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

# Події, які перевіряємо на 48 годин вперед (решта — на 24 години)
EVENTS_48H = {
    "rain", "downpour", "drizzle",
    "snow", "blizzard", "snow_grains",
    "freezing_rain", "ice_pellets",
    "thunderstorm", "hail"
}

SNOW_EVENTS = {"snow", "blizzard", "snow_grains"}

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


def fetch_with_retry(req_or_url, retries: int = 3, timeout: int = 15) -> bytes:
    if isinstance(req_or_url, str):
        parsed = urlparse(req_or_url)
        host = parsed.hostname
        port = parsed.port or 443

        ips = []
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            ips = list(dict.fromkeys(info[4][0] for info in infos))
        except Exception:
            pass

        for ip in ips:
            try:
                cmd = [
                    "curl", "--http1.1", "-4", "-s", "-S",
                    "--connect-timeout", "8", "--max-time", "15",
                    "--resolve", f"{host}:{port}:{ip}",
                    req_or_url
                ]
                res = subprocess.run(cmd, capture_output=True, check=True)
                if res.stdout:
                    return res.stdout
            except Exception as e:
                print(f"Сервер {ip} не відповів: {e}")

        cmd = ["curl", "--http1.1", "-4", "-s", "-S", "--max-time", "25", req_or_url]
        res = subprocess.run(cmd, capture_output=True, check=True)
        return res.stdout

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req_or_url, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            print(f"Спроба {attempt} через urllib не вдалася: {e}")
            if attempt == retries:
                url = req_or_url.full_url
                data = req_or_url.data
                cmd = [
                    "curl", "--http1.1", "-4", "-s", "-S", "--max-time", "25",
                    "-X", "POST", url,
                    "-H", "Content-Type: application/json",
                    "-d", data.decode("utf-8")
                ]
                res = subprocess.run(cmd, capture_output=True, check=True)
                return res.stdout
            time.sleep(3 * attempt)
    return b""


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


def format_snow_amount(snow_cm: float) -> str:
    if snow_cm < 0.5:
        return "незначний (до 0.5 см)"
    rounded = round(snow_cm, 1)
    if rounded.is_integer():
        return f"близько {int(rounded)} см"
    return f"близько {rounded} см"


def calc_episode_snow_cm(
    start_idx: int,
    times: list[str],
    snowfalls: list[float | None]
) -> float:
    total_cm = 0.0
    gap_hours = 0
    max_idx = min(len(times), start_idx + 24)

    for idx in range(start_idx, max_idx):
        sf = snowfalls[idx] or 0.0
        if sf > 0.0:
            total_cm += sf
            gap_hours = 0
        else:
            gap_hours += 1
            if gap_hours > 3:
                break

    return total_cm


def classify_weather(
    code: int,
    gusts_ms: float,
    prob: int | None,
    temp: float | None,
    is_wet: bool,
    had_recent_moisture: bool
) -> list[str]:
    events = []
    has_precip_chance = prob is None or prob >= 40

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

    if gusts_ms >= 15.0:
        events.append("wind")

    if temp is not None and temp <= 0.0:
        if code in (56, 57, 66, 67) or (has_precip_chance and is_wet) or had_recent_moisture:
            events.append("black_ice")

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
    resp_bytes = fetch_with_retry(req, retries=3, timeout=20)
    print("Telegram response:", resp_bytes.decode("utf-8"))


def main() -> None:
    now_kyiv = datetime.now(KYIV_TZ)
    today_str = now_kyiv.strftime("%Y-%m-%d")
    state = load_state()

    print(f"Перевірка погоди для Тернополя на {now_kyiv.strftime('%Y-%m-%d %H:%M')} (Київ)")

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={LAT}&longitude={LON}"
        f"&hourly=weather_code,precipitation_probability,wind_gusts_10m,temperature_2m,precipitation,snowfall"
        f"&wind_speed_unit=ms&timezone=Europe%2FKyiv&forecast_days=3"
    )
    raw_data = fetch_with_retry(url, retries=3, timeout=25)
    data = json.loads(raw_data.decode("utf-8"))

    hourly = data["hourly"]
    times = hourly["time"]
    codes = hourly["weather_code"]
    probs = hourly["precipitation_probability"]
    gusts = hourly["wind_gusts_10m"]
    temps = hourly["temperature_2m"]
    precips = hourly["precipitation"]
    snowfalls = hourly.get("snowfall", [0.0] * len(times))

    detected_events = {}
    horizon_24h = now_kyiv + timedelta(hours=24)
    horizon_48h = now_kyiv + timedelta(hours=48)
    last_wet_dt = None

    max_prob_48h = 0
    max_gust_48h = 0.0
    min_temp_48h = None
    max_temp_48h = None

    for idx, (t_str, code, prob, gust, temp, precip) in enumerate(
        zip(times, codes, probs, gusts, temps, precips)
    ):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=KYIV_TZ)

        is_wet = (precip is not None and precip > 0.1) or (code in WET_WEATHER_CODES)
        had_recent_moisture = (
            last_wet_dt is not None and (dt - last_wet_dt) <= timedelta(hours=6)
        )
        if is_wet:
            last_wet_dt = dt

        if dt <= now_kyiv or dt > horizon_48h:
            continue

        if prob is not None and prob > max_prob_48h:
            max_prob_48h = prob
        if gust is not None and gust > max_gust_48h:
            max_gust_48h = gust
        if temp is not None:
            min_temp_48h = temp if min_temp_48h is None else min(min_temp_48h, temp)
            max_temp_48h = temp if max_temp_48h is None else max(max_temp_48h, temp)

        matched = classify_weather(
            code,
            gust or 0.0,
            prob,
            temp,
            is_wet,
            had_recent_moisture
        )

        for event_key in matched:
            max_horizon = horizon_48h if event_key in EVENTS_48H else horizon_24h
            if dt > max_horizon:
                continue

            if event_key not in detected_events:
                snow_cm = (
                    calc_episode_snow_cm(idx, times, snowfalls)
                    if event_key in SNOW_EVENTS
                    else 0.0
                )
                detected_events[event_key] = {
                    "dt": dt,
                    "prob": prob,
                    "gust": gust,
                    "temp": temp,
                    "snow_cm": snow_cm
                }
            elif (
                event_key == "extreme_heat"
                and temp is not None
                and dt.date() == detected_events[event_key]["dt"].date()
            ):
                current_max = detected_events[event_key]["temp"]
                if current_max is None or temp > current_max:
                    detected_events[event_key]["temp"] = temp

    print(
        f"Зведення за 48 год: макс. імовірність опадів={max_prob_48h}%, "
        f"макс. пориви вітру={round(max_gust_48h, 1)} м/с, "
        f"температура від {min_temp_48h}°C до {max_temp_48h}°C"
    )
    print(f"Знайдені події: {list(detected_events.keys()) if detected_events else 'немає'}")

    state_changed = False

    for event_key, info in detected_events.items():
        if state.get(event_key) == today_str:
            print(f"Подія '{event_key}' вже надсилалася сьогодні ({today_str}), пропускаю.")
            continue

        tpl = WEATHER_TEMPLATES[event_key]
        when_str = format_approx_time(info["dt"], now_kyiv)

        details = f"🕒 <b>Орієнтовний час:</b> {when_str}"
        if event_key in SNOW_EVENTS:
            details += f"\n❄️ <b>Очікується снігу:</b> {format_snow_amount(info['snow_cm'])}"
            if info["prob"] is not None:
                details += f"\n📊 <b>Імовірність:</b> {info['prob']}%"
        elif event_key == "wind" and info["gust"] is not None:
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
