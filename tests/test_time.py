import unittest
from datetime import datetime

import main


class ItalyTashkentTests(unittest.TestCase):
    def item(self, published='2026-01-15T10:00:00+00:00'):
        return main.News('https://www.milannews.it/news/milan-635356', 'Milan', '', published)

    def test_winter_summer_and_next_day(self):
        examples = [
            (datetime(2026, 1, 15, 20, 45), '2026-01-16 00:45 +0500'),
            (datetime(2026, 7, 15, 20, 45), '2026-07-15 23:45 +0500'),
            (datetime(2026, 7, 15, 23, 45), '2026-07-16 02:45 +0500'),
            (datetime(2026, 12, 31, 23, 45), '2027-01-01 03:45 +0500'),
        ]
        for local, expected in examples:
            with self.subTest(local=local):
                self.assertEqual(main.italy_local_to_tashkent(local).strftime('%Y-%m-%d %H:%M %z'), expected)

    def test_dst_nonexistent_and_ambiguous_hours_rejected(self):
        for local in [datetime(2026, 3, 29, 2, 30), datetime(2026, 10, 25, 2, 30)]:
            with self.subTest(local=local):
                with self.assertRaises(ValueError):
                    main.italy_local_to_tashkent(local)

    def test_italian_source_with_explicit_timezone_is_converted(self):
        notes = main.italy_to_tashkent_hints(self.item(), 'Si gioca il 15 gennaio 2026 alle 20:45 ora italiana.')
        self.assertIn('16.01.2026 00:45', notes)

    def test_relative_day_uses_italy_publication_date(self):
        item = self.item('2026-01-15T23:30:00+00:00')  # Already 16 January in Italy.
        notes = main.italy_to_tashkent_hints(item, 'Domani alle 20:45 ora italiana si gioca.')
        self.assertIn('18.01.2026 00:45', notes)

    def test_unknown_zone_is_not_assumed_from_italian_site(self):
        for text in ['15 gennaio 2026 alle 20:45.',
                     '15 gennaio 2026 alle 20:45 ora locale a New York.']:
            with self.subTest(text=text):
                notes = main.italy_to_tashkent_hints(self.item(), text)
                self.assertNotIn('->', notes)

    def test_unknown_date_or_year_is_not_guessed(self):
        for item, text in [(self.item(), 'Alle 20:45 ora italiana.'),
                           (self.item(''), 'Oggi alle 20:45 ora italiana.'),
                           (self.item(''), '15 luglio alle 20:45 ora italiana.')]:
            with self.subTest(text=text):
                self.assertNotIn('->', main.italy_to_tashkent_hints(item, text))

    def test_numeric_date_is_not_misread_as_a_clock(self):
        notes = main.italy_to_tashkent_hints(self.item(), '15.01.2026 alle 20:45 ora italiana.')
        self.assertEqual(notes.count('->'), 1)
        self.assertIn('16.01.2026 00:45', notes)
        self.assertNotIn('15:01', notes)

    def test_dotted_clock_with_explicit_cue_is_supported(self):
        notes = main.italy_to_tashkent_hints(self.item(), '15 gennaio 2026 alle 20.45 ora italiana.')
        self.assertIn('16.01.2026 00:45', notes)

    def test_zone_in_another_sentence_does_not_apply(self):
        notes = main.italy_to_tashkent_hints(self.item(), 'La conferenza usa ora italiana. 15 gennaio 2026 alle 20:45.')
        self.assertNotIn('->', notes)

    def test_mixed_utc_and_italian_clocks_use_the_nearest_explicit_zone(self):
        notes = main.italy_to_tashkent_hints(self.item(), '15 gennaio 2026 alle 19:45 UTC e alle 20:45 ora italiana.')
        self.assertEqual(notes.count('->'), 1)
        self.assertIn('16.01.2026 00:45', notes)
        self.assertNotIn('19:45 ->', notes)

    def test_time_rules_read_full_source(self):
        notes = main.italy_to_tashkent_hints(self.item(), 'Testo lungo. ' * 700 + '\n15 luglio 2026 alle 20:45 ora italiana.')
        self.assertIn('15.07.2026 23:45', notes)

    def test_dst_gap_overlap_and_inconsistent_abbreviation_do_not_generate_hints(self):
        for text in ['29 marzo 2026 alle 02:30 ora italiana.',
                     '25 ottobre 2026 alle 02:30 ora italiana.',
                     '15 luglio 2026 alle 20:45 CET.']:
            with self.subTest(text=text):
                self.assertNotIn('->', main.italy_to_tashkent_hints(self.item(), text))


if __name__ == '__main__':
    unittest.main()
