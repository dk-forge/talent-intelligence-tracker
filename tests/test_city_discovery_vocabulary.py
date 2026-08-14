"""Discovery asks about places, and it asks in a shape that answers.

WHY THIS FILE EXISTS.

`GOOGLE_NEWS_VOCAB` is sixteen language packs of INTENT. Until 2026-08-14 not
one of the forty-nine phrases in it contained a city term, so no city was ever
the subject of a query and a city reached this tracker only when a story we
asked for on other grounds happened to name one. The 2026-08-13 city audit
measured the result: 1,158 of 29,569 current rows carry a city (3.9%), 887 of
them (77%) American, and Cambridge MA, Shanghai, Hangzhou, New Delhi, Bangkok,
Copenhagen, Durham, Osaka and Taipei hold zero.

FOUR PROPERTIES ARE PINNED, and each of them is a way this could be done wrong.

  1. The PLACE LEADS. `backstop_query` already carries the measurement: a query
     that opens with the phrase pack and puts the place at the end returned 0 to
     5 items of which none named the place, while leading with the place
     returned 28 to 54 of which most did. A city query built the wrong way round
     looks like city coverage and delivers the global wire under a city heading.
  2. NO NEW VOCABULARY. Every term in a city query is derived from the phrase
     pack the edition already uses. The standing rule for that file is that a
     phrase is fetched live in its own language before it is committed, and a
     hand-written translation smuggled in through a new function would be
     exactly the guesswork the per-language design exists to prevent.
  3. ONLY THE FIRST TWO PHRASES ARE FLATTENED. Index 2 and after are AND-ed
     groups, so flattening one leaves "raises", "capta" or "raccoglie" standing
     alone as a bare high-frequency token. That is the Czech `investice` trap.
  4. THE VOLUME IS CAPPED. A query is free and a read is not, and the read
     budget is already oversubscribed. Measured on 2026-08-14 over two rotation
     slices of a five-edition run, adding these raised candidates by 7% and by
     20%. An uncapped slice would have asked 138 questions instead of 13.

AND THE CITIES COME FROM THE GAZETTEER, never from a list written here. Asking
about a place the site cannot store is a query that can only ever return work we
throw away.
"""

from __future__ import annotations

import re
import unittest

import source_registry as registry
from pipeline import vocab

DAY = 225
RUN = 0


def city_queries(lang, country):
    return registry.google_news_city_queries(
        lang, country, day_of_year=DAY, run_index=RUN)


class ThePlaceLeads(unittest.TestCase):
    def test_every_city_query_opens_with_the_place(self):
        for lang, country in registry.GOOGLE_NEWS_LOCALES + (registry.GOOGLE_NEWS_ANCHOR,):
            for query in city_queries(lang, country):
                head = query.split(") (", 1)[0]
                terms = re.findall(r'"([^"]+)"', head)
                self.assertTrue(terms, f"{query!r} has no leading place group")
                for term in terms:
                    self.assertIsNotNone(
                        vocab.normalize_city(term),
                        f"{term!r} leads a city query but the gazetteer does "
                        f"not know it, so a story it returns could not be "
                        f"placed. The query vocabulary is derived from the "
                        f"gazetteer for exactly this reason.")

    def test_the_first_quoted_term_of_every_query_is_the_place(self):
        """The strongest form of property 1, and the cheapest to read."""
        for lang, country in registry.GOOGLE_NEWS_LOCALES:
            for query in city_queries(lang, country):
                first = re.findall(r'"([^"]+)"', query)[0]
                self.assertIsNotNone(
                    vocab.normalize_city(first),
                    f"{country}:{lang} opens a city query with {first!r}, "
                    f"which is an intent term and not a place. Google News "
                    f"answers a phrase-led query with the global wire.")


class NoNewVocabulary(unittest.TestCase):
    def test_every_intent_term_appears_verbatim_in_that_languages_pack(self):
        for lang, pack in registry.GOOGLE_NEWS_VOCAB.items():
            group = registry.city_intent_group(lang)
            for term in re.findall(r'"([^"]+)"', group):
                self.assertTrue(
                    any(f'"{term}"' in phrase for phrase in pack),
                    f"{term!r} is in the {lang} city intent group and in none "
                    f"of that language's committed phrases. Every phrase in "
                    f"GOOGLE_NEWS_VOCAB was fetched live in its own language "
                    f"before it was committed; a term invented here has not "
                    f"been.")

    def test_only_the_leadership_and_hiring_phrases_are_flattened(self):
        """The AND-ed funding groups must not become one OR group.

        `("raises" OR ...) ("funding" OR ...)` flattened is `"raises"` standing
        alone, which returns the entire financial wire.
        """
        self.assertEqual(registry.CITY_INTENT_PHRASES, 2)
        group = registry.city_intent_group("en")
        for bare in ("raises", "raised", "secures", "million", "funding"):
            self.assertNotIn(f'"{bare}"', group,
                             f"{bare!r} reached the city intent group. It is "
                             f"one half of an AND-ed pair in the phrase pack "
                             f"and means nothing on its own.")

    def test_a_language_with_no_pack_falls_back_rather_than_inventing(self):
        self.assertEqual(registry.city_intent_group("xx"),
                         registry.city_intent_group("en"))


class TheVolumeIsCapped(unittest.TestCase):
    def test_no_edition_asks_more_than_the_cap(self):
        for lang, country in registry.GOOGLE_NEWS_LOCALES + (registry.GOOGLE_NEWS_ANCHOR,):
            self.assertLessEqual(
                len(city_queries(lang, country)),
                registry.CITY_QUERIES_PER_EDITION,
                f"{country}:{lang} asks more city queries than the cap. Every "
                f"one of them changes what arrives, and arrivals are what the "
                f"gate spends money on.")

    def test_the_rotation_reaches_every_city_of_a_country(self):
        """A cap is only honest if the cities behind it come round."""
        seen = set()
        for day in range(1, 200):
            for run in (0, 1):
                seen.update(registry.city_terms_for_edition(
                    "de", "DE", day_of_year=day, run_index=run))
        self.assertEqual(seen, set(registry.gazetteer_cities()["DE"]))

    def test_the_anchor_asks_only_about_its_own_country(self):
        """Measured, and it is the reason unedited countries are named not asked.

        Widening the anchor to all 202 gazetteer cities drew Noida, Pune and
        Thiruvananthapuram on 2026-08-14 and returned one item between them,
        while pushing any given US city to once every 34 days.
        """
        lang, country = registry.GOOGLE_NEWS_ANCHOR
        us = set(registry.gazetteer_cities()[country])
        for day in range(1, 60):
            for term in registry.city_terms_for_edition(
                    lang, country, day_of_year=day, run_index=0):
                self.assertIn(term, us)

    def test_the_countries_no_edition_reaches_are_named(self):
        gap = registry.unedited_countries()
        for iso2 in ("CN", "IN", "TH", "DK", "TW"):
            self.assertIn(iso2, gap,
                          f"{iso2} has cities on file and no edition, so it "
                          f"belongs in the stated gap rather than nowhere.")


class TheSpellingsWidenWithoutAddingAQuery(unittest.TestCase):
    """Alias spellings go in ONE OR group, so recall rises and volume does not."""

    def test_a_second_spelling_does_not_become_a_second_query(self):
        terms = registry.gazetteer_city_terms()
        multi = [city for cities in terms.values()
                 for city, spellings in cities.items() if len(spellings) > 1]
        self.assertTrue(multi, "no city has a second spelling to test with")
        for lang, country in registry.GOOGLE_NEWS_LOCALES:
            self.assertLessEqual(len(city_queries(lang, country)),
                                 registry.CITY_QUERIES_PER_EDITION)

    def test_short_and_multi_word_aliases_are_dropped(self):
        """"sf", "nyc", "bay area" and "silicon valley" are not place names."""
        for alias, canonical in (("sf", "San Francisco"),
                                 ("nyc", "New York"),
                                 ("bay area", "San Francisco"),
                                 ("silicon valley", "San Francisco")):
            self.assertFalse(registry._worth_querying(alias, canonical),
                             f"{alias!r} would drag half a region into a query")

    def test_a_longer_single_token_alias_is_kept(self):
        self.assertTrue(registry._worth_querying("bengaluru", "Bangalore"))
        self.assertTrue(registry._worth_querying("bombay", "Mumbai"))

    def test_any_non_latin_spelling_is_kept(self):
        """The half the gazetteer supplies, and it is most of the yield.

        Measured 2026-08-14 either side of the native-script aliases landing:
        three Hebrew city queries asked in Latin only returned 0 items between
        them, and with the native spellings in the same OR group ja-JP returned
        103 items from three queries and ko-KR 69 from two. Nothing in this
        file writes a native-script name; this asserts the door stays open.
        """
        self.assertTrue(registry._worth_querying("東京", "Tokyo"))
        self.assertTrue(registry._worth_querying("תל אביב", "Tel Aviv"))

    def test_the_gazetteers_native_spellings_actually_reach_the_query(self):
        """A door that is open and that nothing walks through is still shut."""
        terms = registry.gazetteer_city_terms()
        tokyo = terms.get("JP", {}).get("Tokyo", ())
        self.assertTrue(any(not name.isascii() for name in tokyo),
                        f"Tokyo is asked about as {tokyo!r}, all Latin. The "
                        f"Japanese edition will return almost nothing.")


if __name__ == "__main__":
    unittest.main()
