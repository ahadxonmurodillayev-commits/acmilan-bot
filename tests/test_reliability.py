"""Delivery ledger tests: safe retries without publishing to a real channel."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import main
from telegram.error import BadRequest, RetryAfter, TimedOut


URL = "https://www.milannews.it/news/original-title-635356"
RENAMED_URL = "https://www.milannews.it/calciomercato-milan/updated-title-635356"
ITEM = main.News(URL, "Un commento sul Milan", "Breve descrizione RSS", "2026-01-15T12:00:00+00:00")
UZBEK = (
    "Milan uchun muhim xulosa",
    "🎙️ Mutaxassis jamoa haqida fikr bildirdi.\n\n⚽ Uning fikricha, tarkibni kuchaytirish zarur.",
    "🎙️",
)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "news.sqlite3")
        self.path_patch = patch.object(main, "DB_PATH", self.path)
        self.path_patch.start()
        self.db = main.open_database()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(lambda: self.db.close())

    def seed_completed(self):
        self.db.execute("INSERT OR REPLACE INTO mn_news_settings(key,value) VALUES('seeded_v1','test')")
        self.db.commit()

    def enqueue(self, item=ITEM):
        self.seed_completed()
        main.save_rss(self.db, [item])

    def state(self):
        return self.db.execute("SELECT * FROM mn_news_queue WHERE article_id=?", (main.article_id(URL),)).fetchone()

    def test_first_run_seeding_and_existing_seed_are_preserved(self):
        added, initial = main.save_rss(self.db, [ITEM])
        self.assertEqual((added, initial), (1, True))
        self.assertEqual(main.pending_news(self.db), [])
        main.save_rss(self.db, [main.News(RENAMED_URL, "new title", "summary")])
        self.assertEqual(self.state()["status"], "seed")
        self.assertEqual(self.db.execute("SELECT count(*) FROM mn_news_queue").fetchone()[0], 1)

    def test_same_article_id_different_slug_cannot_repost(self):
        self.enqueue()
        main.mark_sent(self.db, URL, 91)
        main.save_rss(self.db, [main.News(RENAMED_URL, "Updated title", "description")])
        self.assertEqual(main.pending_news(self.db), [])
        self.assertFalse(main.claim_news(self.db, RENAMED_URL))
        self.assertEqual(self.db.execute("SELECT count(*) FROM mn_news_queue").fetchone()[0], 1)

    def test_claim_is_atomic_across_two_connections(self):
        self.enqueue()
        other = main.open_database()
        try:
            self.assertTrue(main.claim_news(self.db, URL))
            self.assertFalse(main.claim_news(other, URL))
            self.assertEqual(self.state()["attempts"], 1)
        finally:
            other.close()

    def test_retry_backoff_is_persisted(self):
        self.enqueue()
        self.assertTrue(main.claim_news(self.db, URL))
        main.release_failed(self.db, URL, "TranslationFailure", retry_seconds=1800)
        self.assertEqual(self.state()["status"], "pending")
        self.assertEqual(main.pending_news(self.db), [])
        self.assertTrue(self.state()["next_attempt_at"])

    def test_restart_recovers_pre_send_work_and_holds_ambiguous_send(self):
        self.enqueue()
        second = main.News("https://www.milannews.it/news/another-635347", "Another", "description")
        main.save_rss(self.db, [second])
        self.db.execute("UPDATE mn_news_queue SET status='processing' WHERE article_id=?", (main.article_id(URL),))
        self.db.execute("UPDATE mn_news_queue SET status='sending' WHERE article_id=?", (main.article_id(second.url),))
        self.db.commit()
        main.recover_inflight(self.db)
        states = {r["article_id"]: r["status"] for r in self.db.execute("SELECT * FROM mn_news_queue")}
        self.assertEqual(states[main.article_id(URL)], "pending")
        self.assertEqual(states[main.article_id(second.url)], "delivery_unknown")
        self.assertFalse(main.claim_news(self.db, second.url))

    def test_opening_another_connection_does_not_release_active_claim(self):
        self.enqueue()
        self.assertTrue(main.claim_news(self.db, URL))
        other = main.open_database()
        try:
            self.assertEqual(self.state()["status"], "processing")
            self.assertFalse(main.claim_news(other, URL))
        finally:
            other.close()

    def test_only_one_worker_can_hold_database_process_lock(self):
        handle = main.acquire_instance_lock()
        try:
            with self.assertRaises(RuntimeError):
                main.acquire_instance_lock()
        finally:
            handle.close()
        main.acquire_instance_lock().close()

    def test_legacy_schema_migration_preserves_sent_over_duplicate_pending(self):
        self.db.close()
        Path(self.path).unlink()
        legacy = sqlite3.connect(self.path)
        legacy.execute("""CREATE TABLE mn_news_queue (
            url TEXT PRIMARY KEY,title TEXT NOT NULL,summary TEXT NOT NULL,
            published TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',
            first_seen TEXT NOT NULL,last_attempt TEXT,attempts INTEGER NOT NULL DEFAULT 0)""")
        legacy.execute("CREATE TABLE mn_news_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        legacy.execute("INSERT INTO mn_news_settings VALUES('seeded_v1','2025-01-01')")
        for url, status in ((URL, "pending"), (RENAMED_URL, "sent")):
            legacy.execute("""INSERT INTO mn_news_queue(url,title,summary,status,first_seen)
                VALUES(?,?,?,?,?)""", (url, "Title", "description", status, "2025-01-01T00:00:00+00:00"))
        legacy.commit()
        legacy.close()
        self.db = main.open_database()
        self.assertEqual(self.db.execute("SELECT count(*) FROM mn_news_queue").fetchone()[0], 1)
        self.assertEqual(self.state()["status"], "sent")
        self.assertEqual(main.pending_news(self.db), [])
        self.assertEqual(self.db.execute("SELECT value FROM mn_news_settings WHERE key='seeded_v1'").fetchone()[0], "2025-01-01")


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "news.sqlite3")
        self.stack = []
        for name, replacement in (
            ("DB_PATH", self.path), ("DRY_RUN", False),
            ("extract_article", Mock(return_value=("Full original article with all paragraphs", None))),
            ("translate", AsyncMock(return_value=UZBEK)),
            ("caption_text", Mock(return_value="<b>O‘zbekcha xabar</b>")),
            ("original_photo_bytes", Mock(return_value=None)),
        ):
            item = patch.object(main, name, replacement)
            item.start()
            self.stack.append(item)
        self.db = main.open_database()
        self.db.execute("INSERT OR REPLACE INTO mn_news_settings(key,value) VALUES('seeded_v1','test')")
        self.db.commit()
        main.save_rss(self.db, [ITEM])
        self.bot = Mock()
        self.bot.send_photo = AsyncMock(return_value=Mock(message_id=81))
        self.bot.send_message = AsyncMock(return_value=Mock(message_id=82))

    async def asyncTearDown(self):
        self.db.close()
        for item in reversed(self.stack):
            item.stop()
        self.directory.cleanup()

    def state(self):
        return self.db.execute("SELECT * FROM mn_news_queue WHERE article_id=?", (main.article_id(URL),)).fetchone()

    async def test_confirmed_send_records_message_and_cannot_repeat(self):
        self.assertTrue(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertEqual(self.state()["status"], "sent")
        self.assertEqual(int(self.state()["message_id"]), 82)
        self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.bot.send_message.assert_awaited_once()
        main.translate.assert_awaited_once()

    async def test_translation_failure_remains_retryable_and_never_posts_italian(self):
        main.translate.side_effect = main.TranslationFailure("translation unavailable")
        self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertEqual(self.state()["status"], "pending")
        self.assertEqual(self.state()["attempts"], 1)
        self.bot.send_message.assert_not_awaited()
        self.bot.send_photo.assert_not_awaited()

    async def test_timeout_is_quarantined_instead_of_automatically_resent(self):
        self.bot.send_message.side_effect = TimedOut("Telegram response was lost")
        self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertEqual(self.state()["status"], "delivery_unknown")
        self.assertEqual(main.pending_news(self.db), [])
        self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.bot.send_message.assert_awaited_once()

    async def test_explicit_rate_limit_is_safe_to_retry(self):
        self.bot.send_message.side_effect = RetryAfter(60)
        self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertEqual(self.state()["status"], "pending")
        self.assertTrue(self.state()["next_attempt_at"])

    async def test_parallel_processing_only_sends_once(self):
        other = main.open_database()
        try:
            results = await asyncio.gather(
                main.process_one(self.bot, None, self.db, ITEM),
                main.process_one(self.bot, None, other, ITEM),
            )
        finally:
            other.close()
        self.assertEqual(sorted(results), [False, True])
        self.bot.send_message.assert_awaited_once()

    async def test_rss_outage_does_not_block_existing_pending_work(self):
        with patch.object(main, "fetch_rss", side_effect=RuntimeError("RSS unavailable")):
            await main.one_cycle(self.bot, None, self.db)
        self.assertEqual(self.state()["status"], "sent")
        self.bot.send_message.assert_awaited_once()

    async def test_slow_delivery_does_not_delay_next_rss_poll(self):
        delivery_started = asyncio.Event()
        delivery_release = asyncio.Event()
        polls_completed = asyncio.Event()
        poll_count = 0

        def fetch_snapshot():
            nonlocal poll_count
            poll_count += 1
            return [ITEM]

        async def blocked_delivery(bot, client, db, item):
            delivery_started.set()
            await delivery_release.wait()
            return False

        original_poll = main.poll_news

        async def record_poll(db):
            result = await original_poll(db)
            if poll_count >= 3:
                polls_completed.set()
            return result

        with patch.object(main, "CHECK_SECONDS", 0.02), \
                patch.object(main, "fetch_rss", side_effect=fetch_snapshot), \
                patch.object(main, "poll_news", side_effect=record_poll), \
                patch.object(main, "process_one", side_effect=blocked_delivery):
            worker = asyncio.create_task(main.run_worker(self.bot, None, self.db))
            try:
                await asyncio.wait_for(delivery_started.wait(), timeout=2)
                await asyncio.wait_for(polls_completed.wait(), timeout=2)
                self.assertFalse(delivery_release.is_set())
                self.assertGreaterEqual(poll_count, 3)
                self.bot.send_message.assert_not_awaited()
                self.bot.send_photo.assert_not_awaited()
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

    async def test_dry_run_never_sends_or_marks_sent(self):
        with patch.object(main, "DRY_RUN", True):
            self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertEqual(self.state()["status"], "pending")
        self.bot.send_message.assert_not_awaited()
        self.bot.send_photo.assert_not_awaited()

    async def test_safe_photo_rejection_falls_back_to_text(self):
        self.bot.send_photo.side_effect = BadRequest("Wrong file identifier/http url specified")
        message_id = await main.send_post(self.bot, "Uzbek post", b"real-image-bytes")
        self.assertEqual(message_id, 82)
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()

    async def test_photo_timeout_does_not_fall_back_to_second_message(self):
        self.bot.send_photo.side_effect = TimedOut("photo response lost")
        with self.assertRaises(TimedOut):
            await main.send_post(self.bot, "Uzbek post", b"real-image-bytes")
        self.bot.send_message.assert_not_awaited()

    async def test_exception_text_and_secrets_are_not_logged_or_persisted(self):
        marker = "123456789:super-secret-telegram-token"
        self.bot.send_message.side_effect = TimedOut(f"https://api.telegram.org/bot{marker}/sendMessage")
        with self.assertLogs(main.log, level=logging.INFO) as captured:
            self.assertFalse(await main.process_one(self.bot, None, self.db, ITEM))
        self.assertNotIn(marker, "\n".join(captured.output))
        self.assertNotIn(marker, str(self.state()["last_error"]))


if __name__ == "__main__":
    unittest.main()
