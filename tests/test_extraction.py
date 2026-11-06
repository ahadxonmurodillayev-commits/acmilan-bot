"""Offline extraction regressions using the three live MilanNews snapshots.

No network requests or Telegram publishing occur in these tests. See the fixture
manifest for source URLs, timestamps and SHA-256 of full retrieved responses.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

import main

FIXTURES = Path(__file__).parent / "fixtures"
RECORDS = json.loads((FIXTURES / "manifest.json").read_text())


def response(url, document, status=200, headers=None):
    result = Mock(status_code=status, url=url, text=document,
                  content=document.encode(), headers=headers or {})
    return result


class ExtractionTests(unittest.TestCase):
    def test_three_real_articles_include_every_paragraph_and_final_argument(self):
        essential = {
            "635356": ("terza figuraccia europea su quattro", "14 e i 16 punti", "prime ventiquattro"),
            "635347": ("Tammy Abraham", "fascia C", "Filippo Terracciano", "Cremonese retrocessa"),
            "635306": ("Bologna", "Rabiot e Moreira", "Modric, Pulisic, Rabiot, Moreira"),
        }
        for record in RECORDS:
            with self.subTest(article=record["id"]):
                document = (FIXTURES / (record["id"] + ".html")).read_text()
                expected = (FIXTURES / (record["id"] + ".txt")).read_text().rstrip()
                item = main.News(record["url"], record["title"], "Only the RSS introduction")
                with patch("main.requests.get", return_value=response(item.url, document)):
                    body, image = main.extract_article(item)
                self.assertEqual(body, expected)
                self.assertEqual(len(body), record["body_chars"])
                self.assertTrue(body.startswith(record["first_sentence"]))
                self.assertTrue(body.endswith(record["last_sentence"]))
                for fact in essential[record["id"]]:
                    self.assertIn(fact, body)
                self.assertNotIn("__tcfapi", body)
                self.assertNotIn("condividi", body)
                self.assertNotIn("Giornalista di MilanNews.it", body)
                self.assertIn("/storage/milannews.it/img_notizie/", image)

    def test_complete_body_is_not_silently_cut_at_old_7500_or_8000_limits(self):
        ending = "Conclusione fondamentale: la rosa richiede equilibrio."
        document = '<div id="tcc-read"><div role="main"><div class="text"><p>'
        document += "Il Milan deve migliorare in tutte le competizioni. " * 220
        document += "</p><p>" + ending + "</p></div></div></div>"
        body, source = main._article_text_from_html(BeautifulSoup(document, "html.parser"))
        self.assertGreater(len(body), 10000)
        self.assertTrue(body.endswith(ending))
        self.assertEqual(source, '#tcc-read [role="main"] .text')

    def test_ad_sidebar_related_links_and_menus_are_removed_but_inline_names_kept(self):
        document = '''<nav>Menu fuori</nav><div class="text">Testo sidebar</div>
        <div id="tcc-read"><div role="main"><div class="text">
        <p>Il <a href="/calciatore">Milan</a> affronta la prossima partita.</p>
        <div id="inRead"><script>advert()</script>Pubblicità</div>
        <div class="related"><a href="/altro-12345">Altro articolo</a></div>
        <p>Rabiot e Pulisic sono i nomi citati nella conclusione.</p>
        <a href="/news-12345">Leggi anche: non è questo articolo</a>
        </div></div><aside>Sidebar lunghissima</aside></div>'''
        body, _ = main._article_text_from_html(BeautifulSoup(document, "html.parser"))
        self.assertIn("Il Milan affronta", body)
        self.assertIn("Rabiot e Pulisic", body)
        for noise in ("Menu", "sidebar", "Sidebar", "Pubblicità", "Altro articolo", "Leggi anche"):
            self.assertNotIn(noise, body)

    def test_full_short_article_need_not_exceed_rss_length(self):
        url = RECORDS[0]["url"]
        text = "Il Milan riprende gli allenamenti domani a Milanello."
        document = '<div id="tcc-read"><div role="main"><div class="text">' + text + '</div></div></div>'
        with patch("main.requests.get", return_value=response(url, document)):
            body, image = main.extract_article(main.News(url, "Title", text))
        self.assertEqual(body, text)
        self.assertIsNone(image)

    def test_missing_body_never_falls_back_to_rss_and_logs_clear_failure(self):
        url = RECORDS[0]["url"]
        item = main.News(url, "Title", "RSS text must not become an article")
        document = '<meta name="description" content="Only intro"><nav>Menu</nav>'
        with patch("main.requests.get", return_value=response(url, document)):
            with self.assertLogs("milannews", level="ERROR") as captured:
                with self.assertRaises(main.ArticleExtractionFailure):
                    main.extract_article(item)
        self.assertIn("ARTICLE_TEXT_UNAVAILABLE", captured.output[0])
        self.assertIn("editorial_body_not_found", captured.output[0])
        self.assertIn("rss_fallback=false", captured.output[0])

    def test_network_failure_does_not_log_exception_credentials(self):
        item = main.News(RECORDS[0]["url"], "Title", "RSS")
        with patch("main.requests.get", side_effect=requests.ConnectionError("secret-key-in-url")):
            with self.assertLogs("milannews", level="ERROR") as captured:
                with self.assertRaises(main.ArticleExtractionFailure):
                    main.extract_article(item)
        self.assertNotIn("secret-key-in-url", "\n".join(captured.output))

    def test_partial_paginated_body_is_explicitly_rejected(self):
        url = RECORDS[0]["url"]
        document = '''<div id="tcc-read"><div role="main"><div class="text">
        <p>Prima parte della lunga intervista.</p><a rel="next" href="?page=2">Continua</a>
        </div></div></div>'''
        with patch("main.requests.get", return_value=response(url, document)):
            with self.assertRaisesRegex(main.ArticleExtractionFailure, "partial_or_paginated"):
                main.extract_article(main.News(url, "Title", "RSS"))

    def test_partial_http_response_is_not_published_as_full_article(self):
        url = RECORDS[0]["url"]
        document = '<div id="tcc-read"><div role="main"><div class="text">Partial body</div></div></div>'
        with patch("main.requests.get", return_value=response(url, document, status=206)):
            with self.assertRaisesRegex(main.ArticleExtractionFailure, "incomplete_http"):
                main.extract_article(main.News(url, "Title", "RSS"))

    def test_non_milannews_redirect_is_not_fetched(self):
        url = RECORDS[0]["url"]
        redirected = response(url, "", status=302, headers={"Location": "https://other.example/news-12345"})
        with patch("main.requests.get", return_value=redirected) as get:
            with self.assertRaisesRegex(main.ArticleExtractionFailure, "non_milannews"):
                main.extract_article(main.News(url, "Title", "RSS"))
        get.assert_called_once()

    def test_jsonld_full_body_is_used_without_description_or_length_clipping(self):
        text = "Il giornalista spiega le ragioni del risultato. " * 200 + "Conclusione completa."
        data = {"@graph": [{"@type": "NewsArticle", "abstract": "Only first sentence", "articleBody": text}]}
        document = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
        body, source = main._article_text_from_html(BeautifulSoup(document, "html.parser"))
        self.assertEqual(body, text)
        self.assertEqual(source, "jsonld.articleBody")

    def test_all_feed_topics_are_retained_and_external_sources_rejected(self):
        xml = b'''<rss><channel>
        <item><title>Women</title><link>https://www.milannews.it/milan-femminile/test-12345</link></item>
        <item><title>Other leagues</title><link>https://www.milannews.it/dall-estero/test-12346</link></item>
        <item><title>External</title><link>https://example.com/test-12347</link></item>
        </channel></rss>'''
        items = main.rss_items(xml)
        self.assertEqual([item.title for item in items], ["Women", "Other leagues"])


if __name__ == "__main__":
    unittest.main()
