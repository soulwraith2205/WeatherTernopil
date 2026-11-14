import html
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Примусово використовуємо IPv4, щоб уникнути таймаутів при завантаженні фото з GitHub Actions
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

SOURCE_CHANNEL = "uameteo"
KYIV_TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = "repost_state.json"

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
raw_channel_id = os.environ["CHANNEL_ID"].strip()
CHANNEL_ID = (
    raw_channel_id
    if raw_channel_id.startswith(("@", "-"))
    else f"@{raw_channel_id}"
)


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_with_retry(req_or_url, retries: int = 3, timeout: int = 25) -> bytes:
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req_or_url, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"Telegram HTTP {e.code}: {err_body}")
            raise
        except Exception:
            if attempt == retries:
                raise
            time.sleep(3 * attempt)
    return b""


def clean_telegram_html(raw_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"<i[^>]*class=\"emoji\"[^>]*><b>(.*?)</b></i>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def parse_posts(page_html: str) -> list[dict]:
    posts = []
    blocks = page_html.split('class="tgme_widget_message_wrap')
    for block in blocks[1:]:
        # Ігноруємо пости зі статтями Telegraph
        if "telegra.ph" in block.lower():
            continue

        id_match = re.search(rf'data-post="{SOURCE_CHANNEL}/(\d+)"', block)
        if not id_match:
            continue
        post_id = int(id_match.group(1))

        text_match = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            block,
            flags=re.DOTALL
        )
        if not text_match:
            continue

        clean_text = clean_telegram_html(text_match.group(1))

        photo_match = re.search(
            r'class="tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:url\(\'(https://[^\']+)\'\)',
            block
        )
        photo_url = html.unescape(photo_match.group(1)) if photo_match else None

        posts.append({
            "id": post_id,
            "text": clean_text,
            "photo_url": photo_url
        })

    posts.sort(key=lambda p: p["id"])
    return posts


def tg_send_message(text: str) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": html.escape(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp_bytes = fetch_with_retry(req, retries=3, timeout=25)
    return json.loads(resp_bytes.decode("utf-8"))


def tg_send_photo_bytes(photo_bytes: bytes, caption: str) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

    fields = {
        "chat_id": CHANNEL_ID,
        "caption": html.escape(caption),
        "parse_mode": "HTML"
    }

    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        b'Content-Disposition: form-data; name="photo"; filename="weather.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
    )
    body.extend(photo_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    raw_body = bytes(body)
    req = urllib.request.Request(
        url,
        data=raw_body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(raw_body))
        },
        method="POST"
    )
    resp_bytes = fetch_with_retry(req, retries=2, timeout=25)
    return json.loads(resp_bytes.decode("utf-8"))


def publish_post(text: str, photo_url: str | None) -> None:
    photo_bytes = None
    if photo_url:
        try:
            img_req = urllib.request.Request(photo_url, headers={"User-Agent": "Mozilla/5.0"})
            photo_bytes = fetch_with_retry(img_req, retries=2, timeout=20)
        except Exception as e:
            print(f"Не вдалося завантажити фото: {e}")

    if photo_bytes:
        try:
            if len(html.escape(text)) <= 1024:
                tg_send_photo_bytes(photo_bytes, text)
                return
            else:
                parts = text.split("\n\n", 1)
                header_caption = parts[0]
                while len(html.escape(header_caption)) > 1020:
                    header_caption = header_caption[:-10]

                tg_send_photo_bytes(photo_bytes, header_caption)
                if len(parts) > 1 and parts[1].strip():
                    tg_send_message(parts[1].strip())
                return
        except Exception as e:
            print(f"Помилка відправки фото, відправляю лише текст: {e}")

    tg_send_message(text)


def main() -> None:
    now_kyiv = datetime.now(KYIV_TZ)
    tomorrow_str = (now_kyiv + timedelta(days=1)).strftime("%d.%m.%Y")
    target_phrase = f"Прогноз погоди на {tomorrow_str}"

    state = load_state()
    last_sent_id = state.get("last_sent_id", 0)
    last_sent_date = state.get("last_sent_for_date", "")

    if last_sent_date == tomorrow_str:
        return

    url = f"https://t.me/s/{SOURCE_CHANNEL}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    page_html = fetch_with_retry(req, retries=3, timeout=25).decode("utf-8", errors="ignore")
    posts = parse_posts(page_html)

    for post in posts:
        if post["id"] <= last_sent_id:
            continue

        if target_phrase in post["text"]:
            publish_post(post["text"], post["photo_url"])
            state["last_sent_id"] = post["id"]
            state["last_sent_for_date"] = tomorrow_str
            save_state(state)
            break


if __name__ == "__main__":
    main()
