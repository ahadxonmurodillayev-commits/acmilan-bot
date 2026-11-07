"""Review the three source articles locally. This command never sends Telegram posts."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402


async def run(args) -> int:
    manifest = json.loads((ROOT / 'tests/fixtures/manifest.json').read_text())
    results = []
    client = None
    if args.live_openai and not main.OPENAI_API_KEY:
        print('OPENAI_API_KEY is missing. No translation or Telegram request was made.', file=sys.stderr)
        return 2
    if args.live_openai:
        client = main.AsyncOpenAI(api_key=main.OPENAI_API_KEY, timeout=45, max_retries=0)
    try:
        for record in manifest:
            item = main.News(record['url'], record['title'], '')
            result = {'article_id': record['id'], 'url': item.url,
                      'source': 'live' if args.refresh_source else 'captured_fixture',
                      'translation': 'not_requested'}
            try:
                if args.refresh_source:
                    body, image_url = await asyncio.to_thread(main.extract_article, item)
                else:
                    body = (ROOT / f"tests/fixtures/{record['id']}.txt").read_text().rstrip('\n')
                    image_url = None
                result.update(source_chars=len(body), source_sha256=hashlib.sha256(body.encode()).hexdigest(),
                              image_url=image_url)
                if client:
                    title, prose, emoji = await main.translate(client, item, body)
                    markup = main.caption_text(title, prose, item.url, emoji)
                    visible = main.BeautifulSoup(markup, 'html.parser').get_text()
                    result.update(translation='validated', caption_html=markup, caption_text=visible,
                                  caption_utf16_units=main.utf16_units(visible))
            except Exception as exc:
                result.update(translation='failed', error_type=type(exc).__name__)
            results.append(result)
    finally:
        if client:
            await client.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(f'Wrote {len(results)} article results to {output}. Telegram sends: 0.')
    return 1 if any(result['translation'] == 'failed' for result in results) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-source', action='store_true', help='Retrieve current MilanNews article pages')
    parser.add_argument('--live-openai', action='store_true', help='Use the configured OpenAI key/model for real local previews')
    parser.add_argument('--output', default='/tmp/acmilan-preview.json')
    raise SystemExit(asyncio.run(run(parser.parse_args())))
