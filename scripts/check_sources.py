#!/usr/bin/env python3
"""Opt-in, read-only MilanNews source check; never calls OpenAI or Telegram.

Run from any directory:
    .venv/bin/python scripts/check_sources.py --output /tmp/milannews-sources.json

The three regression articles and their original photos are downloaded, then the
current rolling RSS window is measured. JSON contains hashes/lengths and factual
anchors, not article bodies, API keys or bot credentials. A finite RSS window
cannot establish that every article during an outage has been discovered.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402 -- standalone script must import repository main.py

ANCHORS = {
    "635356": ["terza figuraccia europea su quattro", "14 e i 16 punti", "prime ventiquattro"],
    "635347": ["Tammy Abraham", "fascia C", "Filippo Terracciano", "Cremonese retrocessa"],
    "635306": ["Bologna", "Rabiot e Moreira", "Modric, Pulisic, Rabiot, Moreira"],
}


def run_check() -> dict:
    manifest = json.loads((ROOT / "tests/fixtures/manifest.json").read_text())
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "actions": "Read-only article, RSS and original-photo downloads; no OpenAI/Telegram calls",
        "articles": [],
        "rss": {},
    }
    for record in manifest:
        result = {"id": record["id"], "url": record["url"]}
        try:
            body, image_url = main.extract_article(main.News(record["url"], record["title"], ""))
            expected = (ROOT / "tests/fixtures" / (record["id"] + ".txt")).read_text().rstrip()
            missing = [anchor for anchor in ANCHORS[record["id"]] if anchor not in body]
            result.update({
                "ok": not missing,
                "body_chars": len(body),
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "matches_captured_fixture": body == expected,
                "missing_expected_factual_anchors": missing,
                "image_url": image_url,
            })
            photo = main.original_photo_bytes(image_url)
            result["photo_downloaded"] = photo is not None
            result["photo_jpeg_bytes"] = len(photo) if photo else 0
            if photo:
                with Image.open(io.BytesIO(photo)) as image:
                    result["photo_dimensions"] = [image.width, image.height]
                    result["photo_format"] = image.format
        except Exception as exc:
            # Exception strings may contain proxy credentials. Stable types are
            # sufficient; extract_article emits a safe, specific failure category.
            result.update({"ok": False, "error_type": type(exc).__name__})
        report["articles"].append(result)
    try:
        items = main.fetch_rss()
        dates = [item.published for item in items if item.published]
        report["rss"] = {
            "ok": True,
            "url": main.RSS_URL,
            "items": len(items),
            "oldest_publication_utc": min(dates, default=None),
            "newest_publication_utc": max(dates, default=None),
            "coverage": "Rolling feed only; not an exhaustive archive or outage backfill",
            "interval_seconds": main.CHECK_SECONDS,
        }
    except Exception as exc:
        report["rss"] = {"ok": False, "error_type": type(exc).__name__}
    report["ok"] = report["rss"]["ok"] and all(item["ok"] for item in report["articles"])
    return report


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Destination JSON file; parent directories are created")
    args = parser.parse_args()
    report = run_check()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Source check {'passed' if report['ok'] else 'failed'}; report: {args.output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(cli())
