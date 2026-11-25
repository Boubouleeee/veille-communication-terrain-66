import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import collect


class FeedParsingTests(unittest.TestCase):
    def test_parses_rss_dates_and_removes_markup(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
          <title>Animation &amp; communication terrain</title>
          <link>https://example.org/article</link>
          <description><![CDATA[<p>Un signal <strong>public</strong>.</p><script>ne pas garder</script>]]></description>
          <pubDate>Wed, 25 Nov 2026 08:00:00 +0100</pubDate>
          <guid>urn:demo:1</guid>
        </item></channel></rss>"""
        entries = collect.parse_feed(xml)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Animation & communication terrain")
        self.assertIn("signal public", entries[0]["summary"])
        self.assertNotIn("ne pas garder", entries[0]["summary"])
        self.assertEqual(entries[0]["publishedAt"], "2026-11-25T07:00:00Z")

    def test_parses_atom_link_and_published_date(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Festival à Perpignan</title><link href="https://example.org/festival"/>
          <updated>2026-11-25T08:00:00+01:00</updated>
        </entry></feed>""".encode("utf-8")
        entries = collect.parse_feed(xml)
        self.assertEqual(entries[0]["url"], "https://example.org/festival")
        self.assertEqual(entries[0]["publishedAt"], "2026-11-25T07:00:00Z")

    def test_rejects_entity_declarations(self):
        with self.assertRaisesRegex(ValueError, "DTD/ENTITY"):
            collect.parse_feed(b'<!DOCTYPE feed [<!ENTITY x "expanded">]><feed/>')


class ScoringAndConfigurationTests(unittest.TestCase):
    def test_preconfigured_public_feeds_have_documented_attribution(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "sources.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(config["sources"]), 2)
        for source in config["sources"]:
            self.assertTrue(source["active"])
            self.assertTrue(source["terms_authorized"])
            self.assertEqual(source["terms_url"], "https://www.service-public.gouv.fr/P10008")
            self.assertEqual(source["location"], "France")
            self.assertEqual(collect.validate_source(source), source)

    def test_local_relevant_item_scores_above_generic_item(self):
        now = datetime(2026, 11, 25, 9, tzinfo=timezone.utc)
        source = {"category": "autre", "location": "Pyrénées-Orientales"}
        local = {"title": "Animation commerciale à Perpignan", "summary": "", "publishedAt": "2026-11-25T08:00:00Z", "feedCategories": ""}
        generic = {"title": "Ouverture administrative", "summary": "", "publishedAt": "2026-11-25T08:00:00Z", "feedCategories": ""}
        local_score, local_category, _, location = collect.score_entry(local, source, now)
        generic_score, _, _, _ = collect.score_entry(generic, source, now)
        self.assertGreater(local_score, generic_score)
        self.assertEqual(generic_score, 0)
        self.assertEqual(local_category, "marketing")
        self.assertEqual(location, "Perpignan")

    def test_source_must_have_reviewed_public_terms_and_https_urls(self):
        source = {
            "id": "public-feed", "name": "Flux public", "url": "https://example.org/feed.xml",
            "category": "institutionnel", "terms_url": "https://example.org/terms",
            "terms_reviewed_at": "2026-11-25", "terms_authorized": True,
        }
        self.assertIs(collect.validate_source(source), source)
        for unsafe in ("http://example.org/feed.xml", "https://127.0.0.1/feed.xml"):
            candidate = {**source, "url": unsafe}
            with self.assertRaises(ValueError):
                collect.validate_source(candidate)
        with self.assertRaisesRegex(ValueError, "port HTTPS"):
            collect.validate_source({**source, "url": "https://example.org:8443/feed.xml"})
        with self.assertRaisesRegex(ValueError, "privée ou réservée"):
            collect.validate_public_target("https://127.0.0.1/feed.xml")

    def test_paris_schedule_uses_summer_and_winter_time(self):
        summer = collect.datetime(2026, 6, 1, 7, tzinfo=timezone.utc).astimezone(collect.PARIS)
        winter = collect.datetime(2026, 12, 1, 8, tzinfo=timezone.utc).astimezone(collect.PARIS)
        self.assertEqual((summer.hour, summer.utcoffset()), (9, collect.timedelta(hours=2)))
        self.assertEqual((winter.hour, winter.utcoffset()), (9, collect.timedelta(hours=1)))

    def test_tracking_parameters_do_not_create_duplicate_signals(self):
        source = {"id": "public-feed", "name": "Flux public", "category": "emploi", "location": "France"}
        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        first = collect.make_entry({
            "title": "Aide à l’entreprise", "url": "https://example.org/article?xtor=RSS-111",
            "summary": "entreprise", "publishedAt": "2026-11-25T08:00:00Z",
        }, source, now)
        second = collect.make_entry({
            "title": "Aide à l’entreprise", "url": "https://example.org/article?xtor=RSS-112",
            "summary": "entreprise", "publishedAt": "2026-11-25T08:00:00Z",
        }, {**source, "id": "another-feed"}, now)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["url"], "https://example.org/article")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "data").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_unconfigured_pipeline_does_not_create_fake_stats(self):
        config = self.root / "config" / "sources.json"
        config.write_text(json.dumps({"sources": []}), encoding="utf-8")
        result = collect.run_collection(self.root, config, datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertEqual(result, 0)
        self.assertFalse((self.root / "data" / "history.json").exists())
        self.assertFalse((self.root / "notes" / "latest.md").exists())
        self.assertEqual(json.loads((self.root / "data" / "collection-status.json").read_text())["status"], "unconfigured")

    def test_total_feed_failure_preserves_last_successful_data(self):
        config = self.root / "config" / "sources.json"
        source = {
            "id": "feed", "name": "Flux public", "url": "https://example.org/feed.xml",
            "category": "institutionnel", "terms_url": "https://example.org/terms",
            "terms_reviewed_at": "2026-11-25", "terms_authorized": True,
        }
        config.write_text(json.dumps({"sources": [source]}), encoding="utf-8")
        latest = self.root / "data" / "latest.json"
        latest.write_text('{"status":"live","entries":[{"id":"prior"}]}', encoding="utf-8")
        with patch.object(collect, "fetch_feed", side_effect=ValueError("test failure")):
            result = collect.run_collection(self.root, config, datetime(2026, 11, 25, tzinfo=timezone.utc))
        self.assertEqual(result, 1)
        self.assertIn("prior", latest.read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.root / "data" / "collection-status.json").read_text())["status"], "error")

    def test_successful_collection_writes_only_scored_real_feed_entries(self):
        config = self.root / "config" / "sources.json"
        source = {
            "id": "local-feed", "name": "Flux local", "url": "https://example.org/feed.xml",
            "category": "institutionnel", "location": "Pyrénées-Orientales",
            "terms_url": "https://example.org/terms", "terms_reviewed_at": "2026-11-25",
            "terms_authorized": True,
        }
        config.write_text(json.dumps({"sources": [source], "minimum_score": 18}), encoding="utf-8")
        feed = """<rss><channel><item><title>Animation commerciale à Perpignan</title>
        <link>https://example.org/article</link><description>Communication terrain</description>
        <pubDate>Wed, 25 Nov 2026 08:00:00 +0000</pubDate></item></channel></rss>""".encode("utf-8")
        with patch.object(collect, "fetch_feed", return_value=feed):
            result = collect.run_collection(self.root, config, datetime(2026, 11, 25, 9, tzinfo=timezone.utc))
        latest = json.loads((self.root / "data" / "latest.json").read_text(encoding="utf-8"))
        history = json.loads((self.root / "data" / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(latest["status"], "live")
        self.assertEqual(len(latest["entries"]), 1)
        self.assertEqual(latest["entries"][0]["category"], "marketing")
        self.assertEqual(history["snapshots"][0]["total"], 1)

    def test_collection_deduplicates_an_article_shared_by_two_feeds(self):
        config = self.root / "config" / "sources.json"
        common = {
            "category": "emploi", "location": "France",
            "terms_url": "https://example.org/terms", "terms_reviewed_at": "2026-11-25",
            "terms_authorized": True,
        }
        sources = [
            {**common, "id": "first-feed", "name": "Flux professionnel", "url": "https://example.org/pro.rss"},
            {**common, "id": "second-feed", "name": "Flux général", "url": "https://example.org/general.rss"},
        ]
        config.write_text(json.dumps({"sources": sources, "minimum_score": 18}), encoding="utf-8")
        feeds = [
            b"""<rss><channel><item><title>Aide exceptionnelle aux entreprises</title>
            <link>https://example.org/article?xtor=RSS-112</link><description>entreprise</description>
            <pubDate>Wed, 25 Nov 2026 08:00:00 +0000</pubDate></item></channel></rss>""",
            b"""<rss><channel><item><title>Aide exceptionnelle aux entreprises</title>
            <link>https://example.org/article?xtor=RSS-111</link><description>entreprise</description>
            <pubDate>Wed, 25 Nov 2026 08:00:00 +0000</pubDate></item></channel></rss>""",
        ]
        with patch.object(collect, "fetch_feed", side_effect=feeds):
            result = collect.run_collection(self.root, config, datetime(2026, 11, 25, 9, tzinfo=timezone.utc))
        latest = json.loads((self.root / "data" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(len(latest["entries"]), 1)
        self.assertEqual(latest["entries"][0]["sourceName"], "Flux professionnel")
        self.assertEqual(latest["entries"][0]["url"], "https://example.org/article")

    def test_weekly_archive_is_immutable(self):
        (self.root / "notes" / "weekly").mkdir(parents=True)
        (self.root / "data" / "latest.json").write_text(json.dumps({
            "status": "live", "entries": [{
                "title": "Publication réelle", "url": "https://example.org/a",
                "summary": "Extrait", "sourceName": "Flux public", "score": 64,
                "publishedAt": "2026-11-25T08:00:00Z",
            }],
        }), encoding="utf-8")
        now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
        self.assertTrue(collect.publish_weekly(self.root, now))
        archive = self.root / "notes" / "weekly" / "2026-W48.md"
        original = archive.read_text(encoding="utf-8")
        latest = json.loads((self.root / "data" / "latest.json").read_text(encoding="utf-8"))
        latest["entries"][0]["title"] = "Titre modifié"
        (self.root / "data" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        collect.publish_weekly(self.root, now)
        self.assertEqual(archive.read_text(encoding="utf-8"), original)
        self.assertNotIn("Titre modifié", original)


if __name__ == "__main__":
    unittest.main()
