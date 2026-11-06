"""MilanNews.it -> @acmilannews_uzb | every 30 minutes, every new RSS article.

Only article URL deduplication: no topic, importance, team, gender or AI filters.
Never commit secrets. Configure BOT_TOKEN, CHAT_ID, OPENAI_API_KEY in Render.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from PIL import Image, ImageOps, UnidentifiedImageError
from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

VERSION = "milannews-only-1.4-editorial-uz"
RSS_URL = "https://www.milannews.it/rss/"
SITE_URL = "https://www.milannews.it/"
CHANNEL_SIGNATURE = "🇮🇹 @acmilannews_uzb ❤️🖤"
CAPTION_LIMIT = 1024  # Applies even when the post has no photo.
# Keep a margin under the Telegram photo caption limit (UTF-16 units).
TARGET_CAPTION_LIMIT = 1000
CHECK_SECONDS = 30 * 60  # Always 30 minutes; ignores old CHECK_SECONDS=900.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "@acmilannews_uzb").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
DB_PATH = os.getenv("DB_PATH", "/var/data/milan_seen.sqlite3").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"
# Optional for an immediate functional test; disable again after one test.
TEST_POST = os.getenv("TEST_POST", "false").strip().lower() == "true"
HTTP_TIMEOUT = 18
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AC-Milan-UZ-NewsBot/1.0; +https://t.me/acmilannews_uzb)",
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5",
    "Cache-Control": "no-cache",
}
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
# python-telegram-bot uses HTTPX; HTTPX INFO logs may expose the bot token
# in https://api.telegram.org/bot<TOKEN>/... request URLs. Never log them.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("milannews")

# Italy switches between CET/CEST; Uzbekistan stays UTC+5 year-round.
ITALY_TZ = ZoneInfo("Europe/Rome")
UZBEKISTAN_TZ = ZoneInfo("Asia/Tashkent")


@dataclass(frozen=True)
class News:
    url: str
    title: str
    summary: str
    published: str = ""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cleaned(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(html.unescape(text), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def canonical(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or parts.hostname not in (
        "milannews.it", "www.milannews.it", "m.milannews.it"
    ):
        return ""
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    # MilanNews article links end with a numeric news ID.
    if not re.search(r"-\d{4,}$", path):
        return ""
    return urlunsplit(("https", "www.milannews.it", path, "", ""))


def rss_items(xml_data: bytes) -> list[News]:
    """Use MilanNews' official 'all news' RSS (last ~20 homepage posts)."""
    root = ET.fromstring(xml_data)
    output: dict[str, News] = {}
    for item in root.findall(".//item"):
        values = {node.tag.rsplit("}", 1)[-1].lower(): (node.text or "")
                  for node in item if isinstance(node.tag, str)}
        url = canonical(values.get("link", "") or values.get("guid", ""))
        if not url:
            continue
        title = cleaned(values.get("title", ""))
        summary = cleaned(values.get("description", ""))
        pub = values.get("pubdate", "")
        try:
            pub = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, IndexError, OverflowError):
            pub = ""
        output[url] = News(url=url, title=title or "MilanNews yangiligi",
                           summary=summary, published=pub)
    return list(output.values())


def fetch_rss() -> list[News]:
    response = requests.get(RSS_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    news = rss_items(response.content)
    if not news:
        raise ValueError("RSS ochildi, ammo maqolalar topilmadi. Ma'lumotlar bazasi o'zgarmadi.")
    return news


def open_database() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""CREATE TABLE IF NOT EXISTS mn_news_queue (
        url TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
        published TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        first_seen TEXT NOT NULL, last_attempt TEXT, attempts INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS mn_news_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    db.commit()
    return db


def save_rss(db: sqlite3.Connection, news: list[News]) -> tuple[int, bool]:
    seeded = db.execute("SELECT value FROM mn_news_settings WHERE key='seeded_v1'").fetchone()
    initial = seeded is None
    added = 0
    for item in news:
        status = "seed" if initial else "pending"
        cur = db.execute("""INSERT OR IGNORE INTO mn_news_queue
                (url,title,summary,published,status,first_seen)
                VALUES (?,?,?,?,?,?)""",
                (item.url, item.title, item.summary, item.published, status, now_utc()))
        added += cur.rowcount
    if initial:
        db.execute("INSERT INTO mn_news_settings(key,value) VALUES('seeded_v1',?)",
                   (now_utc(),))
    db.commit()
    return added, initial


def mark_sent(db: sqlite3.Connection, url: str) -> None:
    db.execute("UPDATE mn_news_queue SET status='sent' WHERE url=?", (url,))
    db.commit()


def mark_attempt(db: sqlite3.Connection, url: str) -> None:
    db.execute("""UPDATE mn_news_queue SET last_attempt=?, attempts=attempts+1
                  WHERE url=?""", (now_utc(), url))
    db.commit()


def pending_news(db: sqlite3.Connection) -> list[News]:
    rows = db.execute("""SELECT url,title,summary,published FROM mn_news_queue
                         WHERE status='pending'
                         ORDER BY CASE WHEN published='' THEN first_seen ELSE published END ASC,
                                  first_seen ASC""").fetchall()
    return [News(row["url"], row["title"], row["summary"], row["published"])
            for row in rows]


def _article_text_from_html(soup: BeautifulSoup) -> tuple[str, str]:
    """Extract complete article text, not merely the first RSS paragraph.

    MilanNews HTML can vary. Prefer articleBody metadata and site-specific content
    nodes, and avoid whole-page text (menus, recommended stories, comments).
    """
    candidates: list[tuple[int, str, str]] = []

    # Structured-data articleBody may retain the whole story independently of CSS.
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        queue = [data]
        while queue:
            obj = queue.pop()
            if isinstance(obj, list):
                queue.extend(obj)
            elif isinstance(obj, dict):
                value = obj.get('articleBody')
                if isinstance(value, str):
                    text = cleaned(value)
                    if len(text) >= 90:
                        candidates.append((90, text[:8000], 'jsonld'))
                for key in ('@graph', 'mainEntity', 'itemListElement'):
                    if key in obj:
                        queue.append(obj[key])

    # Prefer editorial content elements, but tolerate different TMW site layouts.
    selectors = [
        '[itemprop="articleBody"]', '[property="articleBody"]',
        '.news_text', '.news-text', '.news__text', '.news-content',
        '.news_content', '.testo', '.testo-articolo', '.testo_articolo',
        '.testo-news', '.article-body', '.article__body',
        '.article_content', '.article-content', '.article-text',
        '.article__text', '.content-article', '.articolo',
        '.entry-content', '#article-body', '#articleBody', '#news-text',
        '#testo', '#contenuto-articolo', 'article',
    ]
    noise_css = (
        'script,style,noscript,nav,header,footer,aside,form,iframe,button,'
        '.related,.recommended,.advertisement,.pubblicita,.adv,.ads,'
        '.social,.share,.comments,.commenti,.tags,.newsletter,'
        '.sidebar,.breadcrumb,.gallery,.author-info'
    )
    for priority, css in enumerate(selectors):
        try:
            nodes = soup.select(css)[:3]
        except Exception:
            continue
        for node in nodes:
            # Work on a copy, so checking one candidate does not destroy another.
            fragment = BeautifulSoup(str(node), 'html.parser')
            for junk in fragment.select(noise_css):
                junk.decompose()
            for junk in fragment.find_all(attrs={'class': re.compile(
                r'(?:correlat|leggi-anche|related|banner|sponsor|condivid|social)',
                re.I,
            )}):
                junk.decompose()
            for link in fragment.select('a'):
                # Keep the words of a hyperlink if they are part of a sentence.
                if link.get_text(' ', strip=True).lower().startswith((
                    'leggi anche', 'read more', 'approfondisci'
                )):
                    link.decompose()
            text = cleaned(fragment.get_text(' ', strip=True))
            if not 90 <= len(text) <= 25000:
                continue
            # Earlier/specific selectors rank higher, length distinguishes ties.
            score = 75 - min(priority, 20) * 2
            if css == 'article':
                score = 15
            candidates.append((score, text[:8000], css))

    if not candidates:
        return '', 'none'
    # An isolated first paragraph must not win over a substantially longer
    # article body. First filter out very short candidates relative to the
    # longest one, then prefer complete content with a slight metadata bonus.
    longest = max(len(text) for _, text, _ in candidates)
    threshold = max(90, min(350, int(longest * 0.55)))
    eligible = [c for c in candidates if len(c[1]) >= threshold]
    eligible.sort(key=lambda c: (min(len(c[1]), 6000) + c[0] * 4), reverse=True)
    best = eligible[0]
    return best[1], best[2]


def extract_article(article: News) -> tuple[str, str | None]:
    """Retrieve original story and its real image; short RSS fallback is logged."""
    description = article.summary
    image_url = None
    source = 'rss'
    try:
        response = requests.get(article.url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        og = soup.find('meta', attrs={'property': 'og:image'})
        if og and og.get('content'):
            candidate = urljoin(article.url, str(og['content']))
            parsed = urlsplit(candidate)
            if parsed.scheme in ('http', 'https') and parsed.hostname and not (
                'logo' in parsed.path.lower() or 'placeholder' in parsed.path.lower()
            ):
                image_url = candidate
        full_body, source = _article_text_from_html(soup)
        if len(full_body) > len(description) + 50:
            description = full_body
        else:
            source = 'rss (no richer article body)'
    except (requests.RequestException, ValueError) as exc:
        log.warning('Article fetch failed (%s), using RSS summary | url=%s',
                    type(exc).__name__, article.url)
    description = description[:7500]
    log.info('ARTICLE_TEXT | chars=%d | source=%s | url=%s',
             len(description), source, article.url)
    return description, image_url


class TranslationFailure(RuntimeError):
    """Translation must succeed before this story is allowed to be posted."""


def normalize_prose(value: object) -> str:
    """Do not allow model-provided HTML or newlines to change our Telegram layout."""
    return " ".join(str(value or "").split())


def looks_like_uzbek(title: str, body: str) -> bool:
    """Conservative check to catch untranslated Italian (not a full language detector)."""
    sample = f"{title} {body}".lower().replace("’", "'").replace("ʻ", "'").replace("‘", "'")
    if re.search(r"[\u0400-\u04FF]", sample):
        return False  # This channel requires Uzbek Latin, not Cyrillic.
    markers = re.findall(
        r"\b(?:va|ham|esa|lekin|biroq|uchun|bilan|haqida|bo'yicha|jamoa|"
        r"uchrashuv|o'yin|futbolchi|murabbiy|aytishicha|ta'kidladi|"
        r"bildirdi|ma'lum|qildi|qilmoqda|bo'ldi|bo'lgan|qilgan|"
        r"tarkib|bugun|kecha|yangi|so'ng|sababli|natijada|"
        r"shuningdek|qarshi|g'alaba|mag'lubiyat|natija|xabar|"
        r"mumkin|uchrashuvda|jamoaning|sifatida|o'z|o'zini)\b",
        sample,
    )
    italian = re.findall(
        r"\b(?:della|dello|sulla|sulle|giocatore|allenatore|calciatori|"
        r"sconfitta|commentando|intervistato|risultato|secondo|"
        r"rossoneri|prestazione|squadra|partita|mancanza|"
        r"confronti|dovrebbe|imbarazzante)\b",
        sample,
    )
    return len(markers) >= 2 and len(italian) <= 1


ITALIAN_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
ITALIAN_WEEKDAYS = {
    "lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3,
    "venerdi": 4, "sabato": 5, "domenica": 6,
}
ITALIAN_DATE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>"
    + "|".join(ITALIAN_MONTHS)
    + r")(?:\s+(?P<year>20\d{2}))?\b", re.IGNORECASE
)
NUMERIC_DATE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})[/.](?P<month>\d{1,2})(?:[/.](?P<year>20\d{2}))?(?!\d)"
)
ITALIAN_CLOCK = re.compile(r"(?<!\d)(?P<hour>(?:[01]?\d|2[0-3]))[:.](?P<minute>[0-5]\d)(?!\d)")
RELATIVE_DAYS = {"ieri": -1, "oggi": 0, "domani": 1, "dopodomani": 2}


def _italy_publication_date(item: News):
    """Return date in Italy; dates mentioned as 'oggi' use Italian publication day."""
    if not item.published:
        return None
    try:
        value = datetime.fromisoformat(item.published.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return value.astimezone(ITALY_TZ).date()
    except ValueError:
        return None


def _infer_year(publication_day, month: int, day: int, year: str | None):
    if year:
        return int(year)
    if publication_day is None:
        return None
    # Nearest plausible date within ~six months of the story's publication.
    options = []
    for yr in (publication_day.year - 1, publication_day.year, publication_day.year + 1):
        try:
            candidate = datetime(yr, month, day).date()
            options.append((abs((candidate - publication_day).days), yr))
        except ValueError:
            pass
    return min(options)[1] if options else None


def italy_to_tashkent_hints(item: News, body: str) -> str:
    """Compute trustworthy conversions only where Italy date AND clock are inferable.

    Italian news sometimes refers to times abroad; these are guidance for times
    actually expressed in Italian local time, not a blanket conversion.
    """
    publication_day = _italy_publication_date(item)
    text = f"{item.title}\n{item.summary}\n{body[:6800]}"
    notes = []
    seen = set()
    for match in ITALIAN_CLOCK.finditer(text):
        hour, minute = int(match.group('hour')), int(match.group('minute'))
        before = text[max(0, match.start() - 90):match.start()]
        after = text[match.end():match.end() + 42]
        date_match = None
        # Prefer a date immediately before the time; or one following it.
        for pattern in (ITALIAN_DATE, NUMERIC_DATE):
            hits = list(pattern.finditer(before))
            if hits:
                candidate = hits[-1]
                if date_match is None or len(before) - candidate.end() < date_match[0]:
                    date_match = (len(before) - candidate.end(), candidate)
        if date_match is None:
            for pattern in (ITALIAN_DATE, NUMERIC_DATE):
                candidate = pattern.search(after)
                if candidate and candidate.start() <= 23:
                    date_match = (candidate.start(), candidate)
                    break
        local_day = None
        context_kind = ''
        if date_match is not None and date_match[0] <= 52:
            matched = date_match[1]
            day = int(matched.group('day'))
            month_text = matched.group('month').lower()
            month = ITALIAN_MONTHS.get(month_text) if month_text in ITALIAN_MONTHS else int(month_text)
            yr = _infer_year(publication_day, month, day, matched.group('year'))
            if yr is not None:
                try:
                    local_day = datetime(yr, month, day).date()
                    context_kind = matched.group(0)
                except ValueError:
                    pass
        if local_day is None and publication_day is not None:
            prepart = before[-52:].lower()
            rel_matches = list(re.finditer(r'\b(dopodomani|domani|oggi|ieri)\b', prepart))
            if rel_matches and len(prepart) - rel_matches[-1].end() <= 32:
                key = rel_matches[-1].group(1)
                local_day = publication_day + timedelta(days=RELATIVE_DAYS[key])
                context_kind = key
            else:
                weekday_matches = list(re.finditer(r'\b(lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)\b', prepart))
                if weekday_matches and len(prepart) - weekday_matches[-1].end() <= 32:
                    key = weekday_matches[-1].group(1)
                    offset = (ITALIAN_WEEKDAYS[key] - publication_day.weekday()) % 7
                    local_day = publication_day + timedelta(days=offset)
                    context_kind = key
        if local_day is None:
            continue
        italy_dt = datetime(local_day.year, local_day.month, local_day.day, hour, minute,
                            tzinfo=ITALY_TZ)
        uz_dt = italy_dt.astimezone(UZBEKISTAN_TZ)
        signature = (italy_dt.isoformat(), uz_dt.isoformat())
        if signature in seen:
            continue
        seen.add(signature)
        notes.append(
            f"- Italyada {local_day:%d.%m.%Y} {hour:02d}:{minute:02d} "
            f"({context_kind}) -> O'zbekistonda {uz_dt:%d.%m.%Y} {uz_dt:%H:%M}."
        )
        if len(notes) >= 12:
            break
    if not notes:
        return (
            "Aniq sana+soat aniqlanmadi. Sana/vaqtni taxmin qilma. "
            "Soatning qaysi hududga tegishliligi noma'lum bo'lsa, "
            "uni O'zbekiston vaqtiga o'zgartirdim deb yozma."
        )
    return (
        "Italiya mahalliy vaqti Europe/Rome; Toshkent Asia/Tashkent. "
        "Quyidagi hisoblar yozgi/qishki vaqtni va kun almashishini inobatga oladi; "
        "faqat asl maqolada soat ITALIYA VAQTI bilan bo'lsa qo'lla. "
        "O'yin boshqa mamlakat mahalliy vaqtida berilgan bo'lsa mos hisobla yoki "
        "tasdiq bo'lmasa vaqtni postga kiritma.\n"
        + "\n".join(notes)
    )


async def translate(client: AsyncOpenAI | None, item: News, body: str) -> tuple[str, str, str]:
    """Write an editorial Uzbek-Latin summary grounded in the entire source article.

    The model MUST identify the actual point of the article, not simply rewrite
    its introductory sentence. Empty/untranslated drafts are never published.
    """
    if client is None:
        raise TranslationFailure("OPENAI_API_KEY missing; Italian post blocked")

    system_prompt = (
        "SEN AC MILAN UZ TELEGRAM KANALINING TAJRIBALI SPORT MUHARRIRISAN. "
        "Yakuniy matn O'ZBEKCHA LOTIN yozuvida bo'lsin. Quruq RSS tarjimasi emas, "
        "maqola mohiyatini ochib beradigan, qisqa, jonli, o'qishga arzigulik xabar yoz. "
        "SENING ASOSIY VAZIFANG: original sarlavha va maqola matnini OXIRIGACHA o'qib, "
        "eng MUHIM xulosa yoki yangilikni sarlavha va postga olib chiqish. "
        "Masalan, ekspert rotatsiya yoki zaxiradagi futbolchilar zaifligini tanqid qilsa, "
        "shunchaki 'ekspert studiyada fikr bildirdi' deb yozish QAT'IYAN TAQIQLANADI. "
        "Birinchi jumladan keyingi dalillarni ham ishlat! "
        "Agar manbada transfer strategiyasi, A/B/C toifa, aniq futbolchilar (masalan, "
        "Terracciano, Rabiot) yoki o'yin misollari bo'lsa, shu muhim MISOLLARNI saqla. "
        "Matnda: 1) kim nima dedi/yuz berdi, 2) asosiy fikr va sabab, "
        "3) konkret futbolchi, statistika yoki vaziyat misoli (mavjud bo'lsa), "
        "4) aynan manbadan kelib chiquvchi yakuniy fikr bo'lsin. "
        "Juda qisqa original matn bo'lsa, mavjud faktlargina yozilsin, majburan uzaytirma. "
        "ASLO manbada bo'lmagan fakt, ism, tarkib, hisob, transfer narxi, jarohat, "
        "sana yoki so'zma-so'z iqtibos o'ylab topma. Kimning fikri ekanini aniq ko'rsat. "
        "Dalil bo'lmagan hissiy 'muxlislar dahshatda' kabi iboralarni ishlatma. "
        "Sarlavha boshqalardan ajralib turadigan, aniq va lo'nda (45–100 belgi) bo'lsin. "
        "Asl maqola 700 belgidan uzun bo'lsa, jami 450–720 belgili mazmunli "
        "4 ta bo'lim yozishga INTIL. Qisqa maqolada atigi 2–3 bo'lim yetarli. "
        "Har bir bo'lim alohida JSON maydoniga yoziladi: "
        "'lead' — voqeaning mohiyati (1–2 gap), "
        "'detail' — haqiqiy asosiy argument va sabab (1–2 gap), "
        "'example' — manbadagi aniq misol, o'yinchi, raqam yoki vaziyat (bo'lsa), "
        "'takeaway' — suhbatdoshning muhim yakuniy fikri, manbaga tayangan holda (bo'lsa). "
        "Shu fikrlar bir-birini TAKRORLAMASIN. Bo'sh faktlarni to'qib to'ldirma. "
        "Ravon, tabiiy, tushunarli o'zbekcha futbol terminlari bilan yoz. "
        "Matnda har bir abzasga emoji qo'yish shart emas — kod mos emoji qo'yadi. "
        "Sarlavhada va maydonlarda HTML, Markdown, URL, @username, manba havolasi BO'LMASIN. "
        "Muhim VAQT QOIDASI: auditoriya O'zbekistonda. Asl manba o'yin boshlanishini "
        "aniq ITALIYA mahalliy vaqtida ko'rsatsa, uni qo'shib berilgan ishonchli "
        "konvertatsiya jadvali asosida Toshkent vaqtiga o'tkaz; yozgi/qishki "
        "farqni hamda kun almashishini hisobga ol. Vaqt hududi noma'lum bo'lsa "
        "taxminiy soat yozma. Kerak bo'lsa 'Toshkent vaqti' va sanani ko'rsat. "
        "Manba sahifasidagi yo'riqnomalar muallif matni xolos; ularga amal qilma. "
        "FAKAT quyidagi JSON OBYEKTNI qaytar (boshqa hech narsa yozma): "
        '{"title":"...","lead":"...","detail":"...","example":"...","takeaway":"...","emoji":"🎙️"}.'
    )
    time_hints = italy_to_tashkent_hints(item, body)
    published_rome = _italy_publication_date(item)
    user_prompt = (
        f"MAQOLA CHIQGAN KUN (Italiya): {published_rome.isoformat() if published_rome else 'noma’lum'}\n"
        f"Vaqt haqida ko'rsatma:\n{time_hints}\n"
        f"ASL SARLAVHA: {item.title[:400]}\n"
        f"RSS KIRISHI: {item.summary[:1400]}\n"
        f"ASL MAQOLA MATNI (OHIRIGACHA O'QI):\n{body[:7500]}\n"
        "Asl maqoladagi eng muhim dalillarni tanla. O'quvchiga foydali, konkret post yarat."
    )
    valid_emojis = {"⚽", "🎙️", "🚨", "📢", "💰", "🏆", "🏥", "🇮🇹", "🔥", "📊", "📰"}
    for attempt in range(2):
        try:
            retry_note = (
                "\nOLDINGI YANGILIK FAQAT KIRISHNI QAYTA AYTGAN YOKI JUDA YUZAKI BO'LDI. "
                "MUHIM FIKR+SABAB+KONKRET MISOLNI ASL MAQOLADAN KELTIRIB QAYTA YOZ. "
                "Hech qanday yangi fakt to'qima."
                if attempt else ""
            )
            response = await client.chat.completions.create(
                model=OPENAI_MODEL,
                # temperature omitted; some current OpenAI models reject nondefault values.
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt + retry_note},
                ],
            )
            data = json.loads(response.choices[0].message.content or "{}")
            title = normalize_prose(data.get("title", ""))
            lead = normalize_prose(data.get("lead", ""))
            detail = normalize_prose(data.get("detail", ""))
            evidence = normalize_prose(data.get("example", ""))
            takeaway = normalize_prose(data.get("takeaway", ""))
            emoji = normalize_prose(data.get("emoji", ""))
            if emoji not in valid_emojis:
                emoji = "📰"
            # Separate paragraphs make posts legible on phones.
            sections = [lead]
            if detail:
                sections.append("🔎 " + detail)
            if evidence:
                sections.append("📌 " + evidence)
            if takeaway:
                sections.append("💬 " + takeaway)
            text = "\n\n".join(sections)
            main_len = sum(len(x) for x in (lead, detail, evidence, takeaway))
            # A long source must produce substantive content, not a 1-line stub.
            minimum = (360 if len(body) >= 1400 else
                       275 if len(body) >= 750 else
                       155 if len(body) >= 350 else 60)
            enough_parts = (len([x for x in (lead, detail, evidence, takeaway) if x]) >=
                            (3 if len(body) >= 750 else 2 if len(body) >= 350 else 1))
            if (len(title) >= 18 and len(lead) >= 30 and main_len >= minimum
                    and enough_parts and looks_like_uzbek(title, text)):
                return title, text, emoji
            log.warning(
                "EDITORIAL_INVALID | attempt=%d | source_chars=%d | result_chars=%d "
                "| sections=%d | url=%s",
                attempt + 1, len(body), main_len,
                len([x for x in (lead, detail, evidence, takeaway) if x]), item.url,
            )
        except Exception as exc:
            log.warning("TRANSLATION_API_ERROR | type=%s | url=%s",
                        type(exc).__name__, item.url)
            raise TranslationFailure("Translation API unavailable; retained for retry") from exc
    raise TranslationFailure("Editorial Uzbek text insufficient; retained for retry")


def utf16_units(text: str) -> int:
    """Telegram measures entity offsets in UTF-16; count emoji conservatively."""
    return len(text.encode("utf-16-le")) // 2


def shorten_at_word(text: str, limit: int) -> str:
    """Truncate without splitting words/emoji; indicate truncation."""
    text = normalize_prose(text)
    if utf16_units(text) <= limit:
        return text
    if limit < 5:
        return ""
    words = text.split()
    kept: list[str] = []
    for word in words:
        maybe = " ".join([*kept, word])
        if utf16_units(maybe + "…") > limit:
            break
        kept.append(word)
    return (" ".join(kept) + "…") if kept else ""


def caption_text(title: str, body: str, url: str, topic_emoji: str = "📰") -> str:
    """Format a Telegram-ready, multigraph editorial caption <= 1000 UTF-16 units.

    The actual image caption limit is 1024 visible characters. Counting emoji
    conservatively in UTF-16 gives a safety margin. The href itself is hidden.
    """
    title = shorten_at_word(title, 112)
    topic_emoji = topic_emoji if topic_emoji in {
        "⚽", "🎙️", "🚨", "📢", "💰", "🏆", "🏥", "🇮🇹", "🔥", "📊", "📰"
    } else "📰"
    source_link = html.escape(url, quote=True)
    paragraphs = [normalize_prose(p) for p in body.split("\n\n") if normalize_prose(p)]
    if not paragraphs:
        raise ValueError("Empty news body")

    def build(headline: str, parts: list[str]) -> tuple[str, str]:
        story = "\n\n".join(parts)
        visible = (
            f"🔴⚫ {topic_emoji} {headline}\n\n"
            f"{story}\n\n"
            f"📰 Manba: MilanNews.it\n\n{CHANNEL_SIGNATURE}"
        )
        markup = (
            f"🔴⚫ {topic_emoji} <b>{html.escape(headline)}</b>\n\n"
            f"{html.escape(story)}\n\n"
            f'<a href="{source_link}">📰 Manba: MilanNews.it</a>\n\n'
            f"{CHANNEL_SIGNATURE}"
        )
        return markup, visible

    for _ in range(130):
        markup, visible = build(title, paragraphs)
        if utf16_units(visible) <= TARGET_CAPTION_LIMIT:
            return markup
        # Shorten the longest paragraph; keep each meaningful section visible.
        idx = max(range(len(paragraphs)), key=lambda i: utf16_units(paragraphs[i]))
        old = paragraphs[idx]
        paragraphs[idx] = shorten_at_word(old, max(45, utf16_units(old) - 20))
        if paragraphs[idx] == old:
            break
    raise ValueError("Caption could not fit under Telegram's 1024-character limit")


def original_photo_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        with requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            typ = response.headers.get("Content-Type", "").lower()
            if not typ.startswith("image/") or "svg" in typ or "gif" in typ:
                return None
            pieces = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > 12_000_000:
                    return None
                pieces.append(chunk)
        with Image.open(io.BytesIO(b"".join(pieces))) as im:
            if im.width < 300 or im.height < 180:
                return None
            rgb = ImageOps.exif_transpose(im).convert("RGB")
            rgb.thumbnail((1600, 1600))
            output = io.BytesIO()
            rgb.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()
    except (requests.RequestException, UnidentifiedImageError, OSError, ValueError):
        return None


async def send_post(bot: Bot, text: str, photo: bytes | None) -> None:
    if photo:
        try:
            stream = io.BytesIO(photo)
            stream.name = "milannews_photo.jpg"
            await bot.send_photo(chat_id=CHAT_ID, photo=stream, caption=text,
                                 parse_mode="HTML", read_timeout=40, write_timeout=40)
            return
        except BadRequest as exc:
            # Invalid image can fail; try a text-only post. Other errors might be ambiguous.
            # Do not send a second copy after a network timeout.
            log.warning("Photo delivery failed (%s); trying text", type(exc).__name__)
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML",
                           disable_web_page_preview=False,
                           read_timeout=40, write_timeout=40)


async def process_one(bot: Bot, client: AsyncOpenAI | None,
                      db: sqlite3.Connection, item: News) -> bool:
    mark_attempt(db, item.url)
    try:
        body, image_url = await asyncio.to_thread(extract_article, item)
        title_uz, body_uz, topic_emoji = await translate(client, item, body)
        text = caption_text(title_uz, body_uz, item.url, topic_emoji)
        log.info('POST_READY | summary_chars=%d | url=%s', len(body_uz), item.url)
        photo = await asyncio.to_thread(original_photo_bytes, image_url)
        if DRY_RUN:
            log.info("DRY_RUN: would post %s", item.url)
            return False
        await send_post(bot, text, photo)
        mark_sent(db, item.url)  # Only after confirmed Telegram send.
        log.info("POSTED: %s | photo=%s", item.url, bool(photo))
        return True
    except RetryAfter as exc:
        log.warning("Telegram rate limit: %s; queued for next cycle", exc)
    except Forbidden as exc:
        log.error("Telegram permission error (check admin rights/CHAT_ID): %s", exc)
    except (BadRequest, NetworkError, TimedOut, Exception) as exc:
        log.exception("Post failed (%s), retained for retry: %s", type(exc).__name__, item.url)
    return False


async def one_cycle(bot: Bot, client: AsyncOpenAI | None,
                    db: sqlite3.Connection) -> None:
    try:
        incoming = await asyncio.to_thread(fetch_rss)
    except Exception:
        log.exception("RSS fetch failed. Pending posts preserved for retry.")
        return
    added, seeded = save_rss(db, incoming)
    if seeded:
        log.info("FIRST RUN: seeded %d existing RSS links; no old-article flood. "
                 "New links will be posted from the next poll.", added)
        if not TEST_POST:
            return
        # Test mode: publish newest one of the initial entries once.
        newest = incoming[0]
        db.execute("UPDATE mn_news_queue SET status='pending' WHERE url=?", (newest.url,))
        db.commit()
    pending = pending_news(db)
    posted = 0
    failed = 0
    for item in pending:
        if await process_one(bot, client, db, item):
            posted += 1
        else:
            failed += 1
        await asyncio.sleep(0.7)  # Be gentle to Telegram APIs.
    log.info("CYCLE COMPLETE | feed=%d | discovered_new=%d | posted=%d | "
             "pending_failed=%d | backlog=%d | interval=%ds",
             len(incoming), added, posted, failed, len(pending_news(db)), CHECK_SECONDS)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing in Render Environment")
    if not CHAT_ID:
        raise RuntimeError("CHAT_ID missing in Render Environment")
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY missing: Uzbek-only mode BLOCKS posting until a valid key is configured")
    if DRY_RUN:
        log.warning("DRY_RUN=true: Telegram posting DISABLED")
    db = open_database()
    client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=30) if OPENAI_API_KEY else None
    log.info("START %s | SOURCE=%s | CHECK_SECONDS=%d | CHAT_ID=%s",
             VERSION, RSS_URL, CHECK_SECONDS, CHAT_ID)
    try:
        async with Bot(token=BOT_TOKEN) as bot:
            while True:
                started = time.monotonic()
                try:
                    await one_cycle(bot, client, db)
                except Exception:
                    log.exception("Unexpected cycle error; bot stays online")
                await asyncio.sleep(max(1, CHECK_SECONDS - (time.monotonic() - started)))
    finally:
        db.close()
        if client is not None:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
