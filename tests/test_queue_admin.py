"""Manual delivery reconciliation must be explicit, locked and narrow."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scripts import queue_admin


URL = "https://www.milannews.it/news/admin-check-635356"


class QueueAdminTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "news.sqlite3")
        self.path_patch = patch.object(main, "DB_PATH", self.path)
        self.path_patch.start()
        self.db = main.open_database()
        self.db.execute("INSERT INTO mn_news_settings VALUES('seeded_v1','test')")
        self.db.commit()
        main.save_rss(self.db, [main.News(URL, "Title", "description")])
        self.db.execute("UPDATE mn_news_queue SET status='delivery_unknown'")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.path_patch.stop()
        self.directory.cleanup()

    def status(self):
        return self.db.execute("SELECT status,message_id FROM mn_news_queue").fetchone()

    def run_cli(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = queue_admin.main(["--db-path", self.path, *args])
        return result, output.getvalue(), error.getvalue()

    def test_default_lists_uncertain_rows_without_changing_them(self):
        result, output, _ = self.run_cli()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)[0]["article_id"], "635356")
        self.assertEqual(self.status()["status"], "delivery_unknown")

    def test_listing_missing_database_does_not_create_one(self):
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            queue_admin.list_rows(str(missing))
        self.assertFalse(missing.exists())

    def test_resolve_without_explicit_review_flag_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.run_cli("resolve", "--article-id", "635356", "--resolution", "retry")
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.status()["status"], "delivery_unknown")

    def test_confirmed_sent_records_receipt_and_does_not_requeue(self):
        result, _, _ = self.run_cli("resolve", "--article-id", "635356", "--resolution", "sent", "--message-id", "117", "--confirm-reviewed")
        self.assertEqual(result, 0)
        self.assertEqual(self.status()["status"], "sent")
        self.assertEqual(self.status()["message_id"], 117)
        self.assertEqual(main.pending_news(self.db), [])

    def test_confirmed_absent_delivery_returns_to_retry_queue(self):
        result, _, _ = self.run_cli("resolve", "--article-id", "635356", "--resolution", "retry", "--confirm-reviewed")
        self.assertEqual(result, 0)
        self.assertEqual(self.status()["status"], "pending")
        self.assertEqual([item.url for item in main.pending_news(self.db)], [URL])

    def test_known_sent_row_cannot_be_reset_to_pending(self):
        main.mark_sent(self.db, URL, 117)
        result, _, error = self.run_cli("resolve", "--article-id", "635356", "--resolution", "retry", "--confirm-reviewed")
        self.assertEqual(result, 1)
        self.assertIn("Only delivery_unknown", error)
        self.assertEqual(self.status()["status"], "sent")

    def test_running_worker_lock_blocks_resolution(self):
        lock = main.acquire_instance_lock()
        try:
            result, _, _ = self.run_cli("resolve", "--article-id", "635356", "--resolution", "retry", "--confirm-reviewed")
        finally:
            lock.close()
        self.assertEqual(result, 1)
        self.assertEqual(self.status()["status"], "delivery_unknown")


if __name__ == "__main__":
    unittest.main()
