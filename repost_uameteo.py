import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


def fetch_with_retry(req_or_url, retries: int = 3, timeout: int = 30) -> bytes:
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req_or_url, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            if attempt == retries:
                raise
            time.sleep(5 * attempt)
    return b""


def clean_telegram_html(raw_html: str) -> str:
    # Замінюємо <br> на переноси рядків
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    # Витягуємо стандартні емодзі з тегів Telegram Premium (<tg-emoji> або <i class="emoji"><b>...</b></i>)
    text = re.sub(r"<i[^>]*class=\"emoji\"[^>]*><b>(.*?)</b></i>", r"\1", text, flags=re.DOTALL)
    # Видаляємо всі інші HTML-теги, залишаючи чистий текст
    text = re.sub(r"<[^>]+>", "", text)
    # Декодуємо HTML-сутності (&quot;, &amp; тощо)
    text = html.unescape(text)
    # Прибираємо зайві пробіли по краях рядків
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def parse_posts(page_html: str) -> list[dict]:
    posts = []
    # Розбиваємо сторінку на окремі блоки повідомлень
    blocks = page_html.split('class="tgme_widget_message_wrap')
    for block in blocks[1:]:
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

        # Шукаємо перше фото в пості, якщо воно є
        photo_match = re.search(
            r'class="tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:url\(\'(https://[^\']+)\'\)',
            block
        )
        photo_url = photo_match.group(1) if photo_match else None

        posts.append({
            "id": post_id,
            "text": clean_text,
            "photo_url": photo_url
        })

    posts.sort(key=lambda p: p["id"])
    return posts


def tg_api_call(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp_bytes = fetch_with_retry(req, retries=3, timeout=25)
    return json.loads(resp_bytes.decode("utf-8"))


def publish_post(text: str, photo_url: str | None) -> None:
    safe_text = html.escape(text)

    if not photo_url:
        tg_api_call("sendMessage", {
            "chat_id": CHANNEL_ID,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
        return

    # Якщо текст вміщується в ліміт підпису до фото (1024 символи) — відправляємо одним постом
    if len(text) <= 1000:
        tg_api_call("sendPhoto", {
            "chat_id": CHANNEL_ID,
            "photo": photo_url,
            "caption": safe_text,
            "parse_mode": "HTML"
        })
    else:
        # Якщо текст довший — перший абзац (заголовок із датою) йде підписом до фото, решта — одразу нижче
        parts = text.split("\n\n", 1)
        header_caption = html.escape(parts[0][:1000])
        tg_api_call("sendPhoto", {
            "chat_id": CHANNEL_ID,
            "photo": photo_url,
            "caption": header_caption,
            "parse_mode": "HTML"
        })
        if len(parts) > 1 and parts[1].strip():
            tg_api_call("sendMessage", {
                "chat_id": CHANNEL_ID,
                "text": html.escape(parts[1].strip()),
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })


def main() -> None:
    now_kyiv = datetime.now(KYIV_TZ)
    tomorrow_str = (now_kyiv + timedelta(days=1)).strftime("%d.%m.%Y")
    target_phrase = f"Прогноз погоди на {tomorrow_str}"

    state = load_state()
    last_sent_id = state.get("last_sent_id", 0)
    last_sent_date = state.get("last_sent_for_date", "")

    # Якщо прогноз на завтрашню дату вже пересилали — виходимо
    if last_sent_date == tomorrow_str:
        return

    url = f"https://t.me/s/{SOURCE_CHANNEL}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    page_html = fetch_with_retry(req, retries=3, timeout=30).decode("utf-8", errors="ignore")
    posts = parse_posts(page_html)

    for post in posts:
        if post["id"] <= last_sent_id:
            continue

        # Перевіряємо, чи це саме щоденний прогноз на завтра
        if target_phrase in post["text"]:
            publish_post(post["text"], post["photo_url"])
            state["last_sent_id"] = post["id"]
            state["last_sent_for_date"] = tomorrow_str
            save_state(state)
            break


if __name__ == "__main__":
    main()
