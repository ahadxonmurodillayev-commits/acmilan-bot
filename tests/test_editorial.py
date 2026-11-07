"""Offline contract tests with real article sources and explicit fake API replies.

Reference drafts below are human-written test fixtures, not live model results.
They test enforcement, evidence preservation and API contracts. Actual Uzbek
fluency remains a live editorial evaluation when OPENAI_API_KEY is configured.
"""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import APIConnectionError, InternalServerError, RateLimitError, BadRequestError

from bs4 import BeautifulSoup

import main

FIXTURES = Path(__file__).parent / 'fixtures'
MANIFEST = {entry['id']: entry for entry in json.loads((FIXTURES / 'manifest.json').read_text())}
APPROVE = {'grounded': True, 'uzbek_latin': True, 'attribution_correct': True,
           'time_correct': True, 'complete': True, 'missing_fact_ids': [], 'issues': []}


def completion(data):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content=json.dumps(data, ensure_ascii=False)))])


def fake_client(*replies):
    call = AsyncMock(side_effect=[completion(reply) if isinstance(reply, dict) else reply for reply in replies])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call)))


def article(identifier):
    record = MANIFEST[identifier]
    return main.News(record['url'], record['title'], 'RSS kirishi to‘liq maqola emas.'), (FIXTURES / f'{identifier}.txt').read_text().strip()


def fact(identifier, quote, meaning, entities):
    return {'id': identifier, 'source_quote': quote, 'fact_uz': meaning, 'essential': True, 'entities': entities}


CASES = {
    '635347': {
        'facts': [
            fact('f1', 'dalla fascia A', 'Letizia A toifadan C toifaga o‘tishni tanqid qildi.', ['Letizia']),
            fact('f2', 'i Pulisic, anche Pulisic è figlio di questa politica', '20 millionlik siyosat Pulisicni ham olib kelgan.', ['Pulisic']),
            fact('f3', 'Filippo Terracciano è un ragazzo che l\'anno scorso giocava nella Cremonese retrocessa', 'Terracciano va Cremonese misoli tanqidning dalili.', ['Terracciano', 'Cremonese'])],
        'draft': {'title': 'Letizia: Milan A toifadan C toifaga tushib qolmasligi kerak',
                  'lead': 'Francesco Letizia fikricha, Milan tarkibidagi cheklovlar tasodif emas, yozgi transfer tanlovlarining oqibati.',
                  'detail': 'Klub Tammy Abraham kabi B toifadagi futbolchilardan voz kechgan. Letizia Moreira va Gonçalo Ramos darajasidan C toifaga o‘tish hatto Yevropa ligasi uchun ham yetmasligini aytdi.',
                  'example': 'U 20 millionlik transferlar orasida Pulisic kabi foydali xaridlar ham borligini eslatdi. Misol sifatida quyi ligaga tushgan Cremonese safida porlamagan Terracciano endi Milanda asosiy tarkibda ekanini keltirdi.',
                  'takeaway': 'Uning xulosasi: muammo alohida o‘yinchida emas, tarkibni shakllantirishda.',
                  'included_fact_ids': ['f1', 'f2', 'f3']},
        'must_keep': ['Letizia', 'B toifa', 'C toifa', '20 million', 'Pulisic', 'Terracciano', 'Cremonese']},
    '635356': {
        'facts': [
            fact('f1', 'terza figuraccia europea su quattro uscite', 'Pasotto to‘rtta o‘yinda uchinchi yomon chiqishni tanqid qildi.', ['Pasotto']),
            fact('f2', 'servono tra i 14 e i 16 punti, bisognerebbe vincerle tutte', '14–16 ochko va qolgan barcha uchrashuvlarda g‘alaba kerak.', []),
            fact('f3', 'restare fra le prime ventiquattro per guadagnarsi i playoff', 'Eng yaxshi 24 jamoa qatorida qolish xavf ostida.', [])],
        'draft': {'title': 'Pasotto: derbi quvonchidan keyin Yevropada xavotirli mag‘lubiyat',
                  'lead': 'Marco Pasotto fikricha, Milan derbidan keyin Ferencvarosga qarshi yetarli ishtiyoq ko‘rsatmadi. Bu to‘rtta Yevropa o‘yinidagi uchinchi yomon chiqish bo‘ldi.',
                  'detail': 'U faqat charchoq yoki rotatsiyani sabab qilish kamlik qilishini aytdi: 2:0 hisob Ferencvarosning ustunligini to‘liq aks ettirmaydi.',
                  'example': 'Pasotto hisobicha, nimchorak finalga to‘g‘ridan-to‘g‘ri chiqish uchun 14–16 ochko, ya’ni qolgan barcha o‘yinlarda g‘alaba kerak. Endi pley-off uchun eng yaxshi 24 jamoa safida qolish ham e’tibor talab qiladi.',
                  'takeaway': 'Uning xulosasi: Milan keskin o‘zgaruvchan natijalar ko‘rsatmoqda.',
                  'included_fact_ids': ['f1', 'f2', 'f3']},
        'must_keep': ['Pasotto', '2:0', '14–16', '24', 'Ferencvaros']},
    '635306': {
        'facts': [
            fact('f1', 'Amorim ha risparmiato Rabiot e Moreira per averli nel derby', 'Bologna bilan o‘yinda Rabiot va Moreira derbi uchun asralgan.', ['Amorim', 'Rabiot', 'Moreira']),
            fact('f2', "non è ancora così permeato in tutto l'organico", 'Bucciantini fikricha, kerakli ruhiyat hali butun tarkibga singmagan.', ['Bucciantini']),
            fact('f3', 'Modric, Pulisic, Rabiot, Moreira', 'Asosiy futbolchilar bilan reja yaxshiroq ishlaydi.', ['Modric', 'Pulisic'])],
        'draft': {'title': 'Bucciantini: rotatsiyada Milanning sifati keskin pasaymoqda',
                  'lead': 'Marco Bucciantini fikricha, Milan har uch kunda o‘ynaganda tarkibni almashtirishga majbur, ammo jamoaning o‘yini haddan tashqari yomonlashmoqda.',
                  'detail': 'U Bologna bilan uchrashuvni misol qildi: Amorim Rabiot va Moreira kuchini derbiga saqlagan edi.',
                  'example': 'Bucciantini aytishicha, kerakli ruhiyat hali butun tarkibga singmagan. Modric, Pulisic, Rabiot va Moreira maydonda bo‘lsa, rejalangan o‘yinni amalga oshirish osonroq.',
                  'takeaway': 'Uning xulosasi: asosiy futbolchilar almashganda jamoaning barqarorligi yetishmayapti.',
                  'included_fact_ids': ['f1', 'f2', 'f3']},
        'must_keep': ['Bucciantini', 'Bologna', 'Amorim', 'Rabiot', 'Moreira', 'Modric', 'Pulisic']},
}


class EditorialContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sources_keep_key_arguments_names_and_opinion_attribution(self):
        for identifier, case in CASES.items():
            with self.subTest(article=identifier):
                item, body = article(identifier)
                client = fake_client({'facts': case['facts']}, case['draft'], APPROVE)
                title, text, emoji = await main.translate(client, item, body)
                post = main.caption_text(title, text, item.url, emoji)
                visible = BeautifulSoup(post, 'html.parser').get_text()
                self.assertLessEqual(main.utf16_units(visible), 1000)
                self.assertEqual(client.chat.completions.create.await_count, 3)
                for expected in case['must_keep']:
                    self.assertIn(expected, visible)
                for symbol in ['🔴⚫', '🎙️', '⚽', '📌', '❤️🖤']:
                    self.assertIn(symbol, visible)
                self.assertIn('fikricha', visible)
                self.assertIn(main.CHANNEL_SIGNATURE, visible)
                self.assertEqual(BeautifulSoup(post, 'html.parser').a['href'], item.url)
                for call in client.chat.completions.create.await_args_list:
                    self.assertNotIn('temperature', call.kwargs)
                    self.assertIn(body, json.loads(call.kwargs['messages'][1]['content']).get('article', '') or
                                  json.loads(call.kwargs['messages'][1]['content'])['SOURCE']['article'])

    async def test_full_source_tail_reaches_all_stages_without_7500_cutoff(self):
        item, body = article('635347')
        body = body + '\n' + ('Manba sinovi. ' * 700) + ' FINAL_SOURCE_EVIDENCE'
        case = CASES['635347']
        client = fake_client({'facts': case['facts']}, case['draft'], APPROVE)
        await main.translate(client, item, body)
        for call in client.chat.completions.create.await_args_list:
            self.assertIn('FINAL_SOURCE_EVIDENCE', call.kwargs['messages'][1]['content'])

    async def test_reviewer_rejects_superficial_missing_example_and_requests_rewrite(self):
        item, body = article('635347')
        case = CASES['635347']
        rejected = dict(APPROVE, complete=False, missing_fact_ids=['f3'], issues=['Terracciano misolini va sababini saqlang.'])
        client = fake_client({'facts': case['facts']}, case['draft'], rejected, case['draft'], APPROVE)
        await main.translate(client, item, body)
        self.assertEqual(client.chat.completions.create.await_count, 5)
        rewrite_payload = json.loads(client.chat.completions.create.await_args_list[3].kwargs['messages'][1]['content'])
        self.assertIn('Terracciano', ' '.join(rewrite_payload['rewrite_instructions']))

    async def test_oversize_is_rewritten_semantically_and_rechecked(self):
        item, body = article('635347')
        case = CASES['635347']
        long_draft = dict(case['draft'], detail=case['draft']['detail'] * 8)
        client = fake_client({'facts': case['facts']}, long_draft, case['draft'], APPROVE)
        title, text, _ = await main.translate(client, item, body)
        self.assertNotIn('…', text)
        self.assertIn('Terracciano', text)
        self.assertEqual(client.chat.completions.create.await_count, 4)

    async def test_missing_essential_name_is_not_published(self):
        item, body = article('635347')
        case = CASES['635347']
        broken = dict(case['draft'], example='U transferlarni tanqid qildi va jamoaning tarkibi yetarli emasligini aytdi.')
        client = fake_client({'facts': case['facts']}, broken, broken, broken)
        with self.assertRaises(main.TranslationFailure):
            await main.translate(client, item, body)
        self.assertEqual(client.chat.completions.create.await_count, 4)

    async def test_italian_or_cyrillic_is_never_accepted(self):
        for text in ['La squadra della partita non è stata la migliore.', 'Жамоа яхши ўйнади ва ғалаба қилди.']:
            with self.subTest(text=text):
                item, body = article('635347')
                case = CASES['635347']
                bad = dict(case['draft'], lead=text)
                client = fake_client({'facts': case['facts']}, bad, bad, bad)
                with self.assertRaises(main.TranslationFailure):
                    await main.translate(client, item, body)

    async def test_invented_source_quote_is_rejected_before_drafting(self):
        item, body = article('635347')
        invented = fact('f1', 'Messi passa al Milan per cento milioni', 'Yangi transfer.', ['Messi'])
        client = fake_client({'facts': [invented]})
        with self.assertRaises(main.TranslationFailure):
            await main.translate(client, item, body)
        self.assertEqual(client.chat.completions.create.await_count, 1)

    async def test_review_cannot_approve_an_unsupported_fact_or_bad_language(self):
        item, body = article('635347')
        case = CASES['635347']
        no = dict(APPROVE, grounded=False, issues=['Manbada yo‘q faktni chiqaring.'])
        client = fake_client({'facts': case['facts']}, case['draft'], no, case['draft'], no, case['draft'], no)
        with self.assertRaises(main.TranslationFailure):
            await main.translate(client, item, body)

    async def test_api_error_does_not_log_credentials_or_send_italian(self):
        item, body = article('635347')
        client = fake_client(RuntimeError('sk-DO-NOT-LOG botTOKEN-secret'))
        with self.assertLogs('milannews', level='WARNING') as captured:
            with self.assertRaises(main.TranslationFailure):
                await main.translate(client, item, body)
        self.assertNotIn('DO-NOT-LOG', '\n'.join(captured.output))
        self.assertNotIn('botTOKEN-secret', '\n'.join(captured.output))

    async def test_json_repair_retry_is_bounded(self):
        client = fake_client(completion([]), completion({'okay': True}))
        data = await main._editorial_json(client, 'JSON', {})
        self.assertEqual(data, {'okay': True})
        self.assertEqual(client.chat.completions.create.await_count, 2)

    async def test_transient_api_failures_retry_without_exposing_messages(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/chat/completions')
        examples = [APIConnectionError(request=request),
                    RateLimitError('sk-secret', response=httpx.Response(429, request=request), body={}),
                    InternalServerError('sk-secret', response=httpx.Response(503, request=request), body={})]
        for error in examples:
            with self.subTest(error=type(error).__name__):
                client = fake_client(error, {'okay': True})
                with patch.object(main.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
                    data = await main._editorial_json(client, 'JSON', {})
                self.assertEqual(data, {'okay': True})
                sleep.assert_awaited_once_with(1)
                self.assertEqual(client.chat.completions.create.await_count, 2)

    async def test_incompatible_json_mode_has_clear_safe_failure(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/chat/completions')
        error = BadRequestError('sk-private-response', response=httpx.Response(400, request=request),
                                body={'param': 'response_format', 'code': 'unsupported_parameter'})
        client = fake_client(error)
        with self.assertLogs('milannews', level='ERROR') as captured:
            with self.assertRaisesRegex(main.TranslationFailure, 'OPENAI_MODEL must support JSON'):
                await main._editorial_json(client, 'JSON', {})
        self.assertNotIn('sk-private-response', ' '.join(captured.output))
        self.assertEqual(client.chat.completions.create.await_count, 1)

    async def test_missing_api_key_blocks_translation(self):
        item, body = article('635347')
        with self.assertRaises(main.TranslationFailure):
            await main.translate(None, item, body)


class CaptionTests(unittest.TestCase):
    def test_full_visible_caption_limit_includes_emoji_source_and_signature(self):
        item, _ = article('635347')
        title = 'Milan uchun muhim yangilik'
        overhead = BeautifulSoup(main.caption_text(title, 'x', item.url), 'html.parser').get_text()
        body = 'x' * (1000 - main.utf16_units(overhead) + 1)
        markup = main.caption_text(title, body, item.url)
        self.assertEqual(main.utf16_units(BeautifulSoup(markup, 'html.parser').get_text()), 1000)
        with self.assertRaises(ValueError):
            main.caption_text(title, body + '😀', item.url)

    def test_oversize_never_silently_clips_a_key_fact(self):
        item, _ = article('635347')
        with self.assertRaises(ValueError):
            main.caption_text('Milan haqidagi yangi xabar', 'a ' * 800 + 'Terracciano dalili.', item.url)

    def test_html_is_escaped_and_link_is_only_milannews(self):
        item, _ = article('635347')
        markup = main.caption_text('Milan <b> uchun', '🎙️ A & B bilan o‘yin.', item.url)
        soup = BeautifulSoup(markup, 'html.parser')
        self.assertEqual(len(soup.find_all('b')), 1)
        self.assertIn('<b>', soup.b.get_text())
        self.assertIn('A & B', soup.get_text())
        with self.assertRaises(ValueError):
            main.caption_text('Milan uchun yangi xabar', 'Tafsilotlar bor.', 'https://example.org/news-12345')


if __name__ == '__main__':
    unittest.main()
