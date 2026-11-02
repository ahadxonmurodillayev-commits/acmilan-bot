import asyncio
import hashlib
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from telegram import Bot

SOURCES = [
    'https://www.milannews.it/', 'https://sempremilan.com/',
    'https://www.pianetamilan.it/', 'https://www.milanlive.it/',
    'https://www.acmilan.com/', 'https://www.gazzetta.it/',
    'https://gianlucadimarzio.com/', 'https://www.calciomercato.com/',
    'https://www.tuttomercatoweb.com/',
    'https://www.sportmediaset.mediaset.it/',
]
CHAT_ID = os.getenv('CHAT_ID', '@acmilannews_uzb')
BOT_TOKEN = os.environ['BOT_TOKEN']
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']
MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
CHECK_SECONDS = 3600
DB_PATH = os.getenv('DB_PATH', 'milan_seen.sqlite3')
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MilanNewsBot/1.0)'})
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('milanbot')
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def canonical(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip('/'), '', '', ''))


def connect_db():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute('CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, created_at TEXT NOT NULL)')
    db.commit()
    return db


def seen(db, url):
    return db.execute('SELECT 1 FROM seen WHERE url=?', (url,)).fetchone() is not None


def mark(db, url):
    db.execute('INSERT OR IGNORE INTO seen VALUES (?,?)', (url, datetime.now(timezone.utc).isoformat()))
    db.commit()


def fetch(url):
    r = SESSION.get(url, timeout=18)
    r.raise_for_status()
    return BeautifulSoup(r.text, 'html.parser')


def discover(source):
    soup = fetch(source)
    host = urlparse(source).netloc.removeprefix('www.')
    items = []
    for a in soup.select('article a[href], h2 a[href], h3 a[href]'):
        title = a.get_text(' ', strip=True)
        if len(title) < 35:
            continue
        url = canonical(urljoin(source, a.get('href', '')))
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or host not in parsed.netloc:
            continue
        if url == canonical(source):
            continue
        items.append((url, title))
    return list(dict((url, (url, title)) for url, title in items).values())[:12]


def article_data(url):
    soup = fetch(url)
    for x in soup.select('script,style,nav,footer,header,aside'):
        x.decompose()
    title = (soup.find('h1') or soup.find('title'))
    title = title.get_text(' ', strip=True) if title else ''
    root = soup.find('article') or soup.find('main') or soup
    paras = [p.get_text(' ', strip=True) for p in root.select('p')]
    body = ' '.join(p for p in paras if len(p) > 45)[:6500]
    meta = soup.find('meta', property='og:image')
    image = urljoin(url, meta.get('content', '')) if meta else None
    if image and urlparse(image).scheme not in ('http', 'https'):
        image = None
    return title, body, image


async def write_post(title, body, url):
    if len(body) < 180:
        return None
    response = await client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        messages=[
            {'role': 'system', 'content': (
                'You are a cautious Uzbek Latin football news editor for an AC Milan fan channel. '
                'Use ONLY the provided article; do not invent quotes, scores, dates or names. '
                'If the article is not substantially about AC Milan, return SKIP. '
                'If it is a rumor, clearly label it unconfirmed. '
                'Write 2-4 short paragraphs with a few relevant emoji. '
                'No headline, hashtags, source links or closing question. '
                'Do not include a signature; it will be appended by software. '
                'If article text is incomplete, untrustworthy, unrelated, or cannot support a factual post, return SKIP.'
            )},
            {'role': 'user', 'content': f'Title: {title}\nURL: {url}\nArticle: {body}'},
        ],
        max_tokens=450,
    )
    text = (response.choices[0].message.content or '').strip()
    if not text or text.upper().startswith('SKIP'):
        return None
    return text[:3400].rstrip() + '\n\n🇮🇹 @acmilannews_uzb ❤️🖤'


async def publish(bot, text, image, url):
    if DRY_RUN:
        log.info('DRY_RUN (not posted): %s %s', url, text[:120])
        return
    if image:
        try:
            if len(text) <= 1024:
                await bot.send_photo(chat_id=CHAT_ID, photo=image, caption=text)
                return
            await bot.send_photo(chat_id=CHAT_ID, photo=image)
        except Exception as exc:
            log.warning('Image failed for %s: %s', url, exc)
    await bot.send_message(chat_id=CHAT_ID, text=text)


async def cycle(bot, db, first_run=False):
    candidates = []
    for source in SOURCES:
        try:
            candidates.extend(await asyncio.to_thread(discover, source))
        except Exception as exc:
            log.warning('Source failed %s: %s', source, exc)
    unique = dict(candidates)
    log.info('Discovered %d unique links', len(unique))
    if first_run:
        for url in unique:
            mark(db, url)
        log.info('First run: seeded seen links; no historical posts sent')
        return
    for url, fallback_title in list(unique.items())[:70]:
        if seen(db, url):
            continue
        try:
            title, body, image = await asyncio.to_thread(article_data, url)
            post = await write_post(title or fallback_title, body, url)
            if post:
                await publish(bot, post, image, url)
                log.info('Processed %s', url)
            mark(db, url)
        except Exception:
            log.exception('Failed processing %s; will retry next cycle', url)


async def main():
    db = connect_db()
    first_run = db.execute('SELECT COUNT(*) FROM seen').fetchone()[0] == 0
    async with Bot(token=BOT_TOKEN) as bot:
        while True:
            try:
                await cycle(bot, db, first_run=first_run)
                first_run = False
            except Exception:
                log.exception('Cycle failed')
            await asyncio.sleep(CHECK_SECONDS)


if __name__ == '__main__':
    asyncio.run(main())
