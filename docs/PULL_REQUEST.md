Fix full MilanNews extraction and preserve evidence in Uzbek Telegram posts

MilanNews stores the article in `#tcc-read [role="main"] .text`, which the previous extractor never selected. All three reported articles consequently fell back to introductory RSS text, so the model could not see the expert's arguments or examples.

This change reads the complete editorial body, removes advertising/navigation, and retains the original article photo. Missing or partial bodies fail visibly and stay queued. The full source passes through evidence extraction, Uzbek post drafting and a separate editorial review; essential names and source evidence are checked before publication. Overlong captions are rewritten, keeping the complete visible post below 1000 UTF-16 units.

Delivery now deduplicates by MilanNews article ID, atomically claims queued work, persists draft/message state, and holds uncertain Telegram deliveries for channel review rather than automatically resending. Existing SQLite sent/seed history and Environment Variables are retained. Independent RSS polling remains at 30 minutes even while the translation queue is busy. A local review utility, source/translation preview scripts, Render configuration example and CI are included.

Validation:

- Three reported source URLs fetched live with full bodies of 1539, 1696 and 971 characters; original 1200×674 JPEGs downloaded for all three. Evidence and hashes are in `docs/source-check.json` and `tests/fixtures/manifest.json`.
- Automated tests cover complete extraction, concrete evidence/opinion attribution, caption limits, Uzbek-Latin rejection checks, API errors, Italy/Tashkent DST and date rollover, SQLite migration/concurrency, delivery retries/uncertainty, photo fallback and manual reconciliation. Exact results are in `docs/test-results.txt`.
- `compileall`, dependency compatibility (`pip check`), and whitespace checks passed.
- No live OpenAI quality claim: no API key was configured in the review environment. Model contract tests use explicit fixtures; `scripts/preview_articles.py --live-openai` enables a local review without Telegram sends.

Operational limits: the official RSS exposes only the latest 20 homepage items, so outage/burst coverage beyond that feed requires a verified MilanNews archive backfill. Telegram provides no idempotency key; uncertain deliveries need manual channel review. Run one worker on the existing persistent SQLite disk.

No Render deployment, production setting change, channel test post, merge or auto-merge is included. Render auto-deploy is disabled in the example configuration; applying that configuration still requires owner approval. See `README.md` and `docs/AUDIT.md` before deployment.
