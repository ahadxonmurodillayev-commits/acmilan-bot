"""Inspect uncertain deliveries, then resolve only after checking the channel.

Run from any directory: python /path/to/acmilan-bot/scripts/queue_admin.py --help
This utility never contacts Telegram or OpenAI.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Allow running the script by its absolute path outside the repository directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as bot_main  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db-path", default=bot_main.DB_PATH, help="SQLite path; defaults to DB_PATH")
    commands = result.add_subparsers(dest="command")
    listing = commands.add_parser("list", help="Read the ledger without sending or changing statuses")
    listing.add_argument("--status", choices=("delivery_unknown", "pending", "sent", "all"), default="delivery_unknown")
    resolve = commands.add_parser("resolve", help="Record a manual channel review of an uncertain delivery")
    resolve.add_argument("--article-id", required=True)
    resolve.add_argument("--resolution", choices=("sent", "retry"), required=True)
    resolve.add_argument("--message-id", type=int, help="Confirmed Telegram message ID, if available")
    resolve.add_argument("--confirm-reviewed", action="store_true", help="Confirm that you checked the channel for this article")
    return result


def list_rows(path: str, status: str = "delivery_unknown") -> list[dict]:
    """Use SQLite's read-only mode: listing never initializes/migrates a DB."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        query = """SELECT article_id,url,status,message_id,last_error,last_attempt,attempts
            FROM mn_news_queue"""
        values = ()
        if status != "all":
            query += " WHERE status=?"
            values = (status,)
        query += " ORDER BY first_seen,article_id"
        return [dict(row) for row in db.execute(query, values)]


def resolve_row(path: str, article_id: str, resolution: str, message_id: int | None) -> None:
    """Hold the worker lock; change only a previously uncertain delivery."""
    if not article_id.isdigit() or len(article_id) < 4:
        raise ValueError("article-id must be the numeric MilanNews article ID")
    if message_id is not None and message_id < 1:
        raise ValueError("message-id must be positive")
    if resolution == "retry" and message_id is not None:
        raise ValueError("message-id applies only to resolution=sent")
    previous_path = bot_main.DB_PATH
    bot_main.DB_PATH = str(Path(path).resolve())
    lock = None
    db = None
    try:
        lock = bot_main.acquire_instance_lock()
        if not Path(path).is_file():
            raise ValueError("Database does not exist; no resolution was recorded")
        db = bot_main.open_database()
        row = db.execute("SELECT url,status FROM mn_news_queue WHERE article_id=?", (article_id,)).fetchone()
        if row is None:
            raise ValueError("Article is absent from this database")
        if row["status"] != "delivery_unknown":
            raise ValueError("Only delivery_unknown rows can be resolved")
        if resolution == "sent":
            bot_main.mark_sent(db, row["url"], message_id)
        else:
            db.execute("""UPDATE mn_news_queue SET status='pending',next_attempt_at=NULL,
                message_id=NULL,last_error='manually_reviewed_not_sent'
                WHERE article_id=? AND status='delivery_unknown'""", (article_id,))
            db.commit()
    finally:
        if db is not None:
            db.close()
        if lock is not None:
            lock.close()
        bot_main.DB_PATH = previous_path


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.command == "resolve" and not args.confirm_reviewed:
        cli.error("resolve requires --confirm-reviewed after manually checking the channel")
    try:
        if args.command == "resolve":
            resolve_row(args.db_path, args.article_id, args.resolution, args.message_id)
            print(json.dumps({"article_id": args.article_id, "resolution": args.resolution}))
        else:
            print(json.dumps(list_rows(args.db_path, getattr(args, "status", "delivery_unknown")), ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        # Messages here are application errors; never emit exception tracebacks.
        print(f"Queue operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
