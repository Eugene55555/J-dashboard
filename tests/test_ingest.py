"""Тесты загрузчика. Запуск без внешних зависимостей:

    python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.detect import activity_datasets, settings_kind  # noqa: E402
from ingest.load import connect, import_changes, load_activity, load_settings  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class DetectTests(unittest.TestCase):
    def test_campaign_report_feeds_only_campaign_grain(self):
        self.assertEqual(activity_datasets({"date.value", "campaign.id"}), ["campaign_day"])

    def test_detail_report_feeds_every_supported_grain(self):
        keys = {"date.value", "campaign.id", "adGroup.id", "adGroupTarget.id",
                "searchTerm.value", "placementClassification.value", "ad.id",
                "advertisedProduct.id"}
        self.assertEqual(activity_datasets(keys), [
            "adgroup_day", "target_day", "searchterm_day", "placement_day", "product_day",
        ])
        self.assertEqual(
            activity_datasets({"date.value", "campaign.id", "adGroup.id"}),
            ["adgroup_day"],
        )

    def test_settings_kind_detects_negative_variants(self):
        self.assertEqual(settings_kind({"campaignId": "1", "budget": {"budget": 1}}), "campaigns")
        self.assertEqual(settings_kind({"adGroupId": "1", "defaultBid": 0.2}), "adgroups")
        self.assertEqual(settings_kind({"adId": "1", "asin": "B0"}), "ads")
        self.assertEqual(settings_kind({"targetId": "1", "expression": [], "bid": 0.2}), "targets")
        self.assertEqual(settings_kind({"targetId": "1", "expression": [], "adGroupId": "2"}), "negative_targets")
        self.assertEqual(settings_kind({"targetId": "1", "expression": []}), "campaign_negative_targets")
        self.assertEqual(settings_kind({"keywordId": "1", "matchType": "EXACT", "bid": 0.2}), "keywords")
        self.assertEqual(
            settings_kind({"keywordId": "1", "matchType": "NEGATIVE_EXACT", "adGroupId": "2"}),
            "negative_keywords",
        )
        self.assertEqual(
            settings_kind({"keywordId": "1", "matchType": "NEGATIVE_EXACT"}),
            "campaign_negative_keywords",
        )


class FactTests(Base):
    def test_report_rows_are_summed_by_grain(self):
        rows = [
            {"date.value": "2026-11-20", "campaign.id": "1", "campaign.name": "A", "adGroup.id": "10",
             "adGroupTarget.id": "100", "metric.impressions": "10", "metric.clicks": "1",
             "metric.totalCost": "0.50", "metric.purchases": "0", "metric.sales": "0",
             "metric.unitsSold": "0"},
            {"date.value": "2026-11-20", "campaign.id": "1", "campaign.name": "A", "adGroup.id": "10",
             "adGroupTarget.id": "100", "metric.impressions": "5", "metric.clicks": "1",
             "metric.totalCost": "0.25", "metric.purchases": "1", "metric.sales": "9.99",
             "metric.unitsSold": "1"},
        ]
        self.assertEqual(load_activity(self.conn, "target_day", rows, "test", "2026-11-23T00:00:00+00:00"), 1)
        fact = self.conn.execute("SELECT * FROM fact_target_day").fetchone()
        self.assertEqual(fact["impressions"], 15)
        self.assertEqual(fact["clicks"], 2)
        self.assertAlmostEqual(fact["spend"], 0.75)
        self.assertEqual(fact["orders"], 1)
        self.assertEqual(fact["units"], 1)

    def test_rerun_replaces_but_does_not_duplicate(self):
        rows = [{"date.value": "2026-11-20", "campaign.id": "1", "campaign.name": "A",
                 "metric.impressions": "3", "metric.clicks": "1", "metric.totalCost": "0.2"}]
        load_activity(self.conn, "campaign_day", rows, "test", "2026-11-23T00:00:00+00:00")
        rows[0]["metric.impressions"] = "9"
        load_activity(self.conn, "campaign_day", rows, "test", "2026-11-23T01:00:00+00:00")
        facts = self.conn.execute("SELECT * FROM fact_campaign_day").fetchall()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["impressions"], 9)


class SettingsTests(Base):
    def test_snapshot_creates_then_diffs(self):
        campaigns = [{
            "campaignId": "1", "name": "A", "state": "ENABLED",
            "budget": {"budget": 5, "budgetType": "DAILY"},
            "dynamicBidding": {"strategy": "LEGACY_FOR_SALES", "placementBidding": []},
            "targetingType": "MANUAL",
            "extendedData": {"lastUpdateDateTime": "2026-11-20T10:00:00Z"},
        }]
        first = load_settings(self.conn, "campaigns", campaigns, "2026-11-20T12:00:00+00:00",
                              emit_creates=False)
        self.assertEqual(first["new"], 1)
        # первичная загрузка не создаёт событий «создан»
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM changes WHERE field='CREATED'").fetchone()[0], 0
        )

        campaigns[0]["budget"]["budget"] = 9
        campaigns[0]["extendedData"]["lastUpdateDateTime"] = "2026-11-21T09:30:00Z"
        second = load_settings(self.conn, "campaigns", campaigns, "2026-11-21T12:00:00+00:00")
        self.assertEqual(second["updated"], 1)
        change = self.conn.execute("SELECT * FROM changes WHERE field='BUDGET_AMOUNT'").fetchone()
        self.assertEqual(change["old_value"], "5")
        self.assertEqual(change["new_value"], "9")
        self.assertEqual(change["change_ts"], "2026-11-21T09:30:00Z")
        self.assertEqual(change["change_date"], "2026-11-21")

        third = load_settings(self.conn, "campaigns", campaigns, "2026-11-22T12:00:00+00:00")
        self.assertEqual(third["updated"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM changes WHERE field='BUDGET_AMOUNT'").fetchone()[0], 1
        )

    def test_new_object_after_first_snapshot_is_logged(self):
        base = [{"campaignId": "1", "name": "A", "state": "ENABLED", "budget": {"budget": 5},
                 "targetingType": "MANUAL", "dynamicBidding": {"strategy": "LEGACY_FOR_SALES"},
                 "extendedData": {"creationDateTime": "2026-11-01T10:00:00Z"}}]
        load_settings(self.conn, "campaigns", base, "2026-11-20T12:00:00+00:00", emit_creates=False)
        extra = base + [{"campaignId": "2", "name": "B", "state": "PAUSED", "budget": {"budget": 3},
                         "targetingType": "MANUAL", "dynamicBidding": {"strategy": "LEGACY_FOR_SALES"},
                         "extendedData": {"creationDateTime": "2026-11-21T10:00:00Z"}}]
        load_settings(self.conn, "campaigns", extra, "2026-11-21T12:00:00+00:00")
        created = self.conn.execute("SELECT * FROM changes WHERE field='CREATED'").fetchall()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["entity_id"], "2")
        self.assertEqual(created[0]["change_date"], "2026-11-21")

    def test_negative_bid_is_not_invented(self):
        rows = [{
            "targetId": "77", "campaignId": "1", "adGroupId": "2", "state": "ENABLED",
            "expression": [{"type": "ASIN_SAME_AS", "value": "B0TEST"}],
            "extendedData": {"lastUpdateDateTime": "2026-11-20T10:00:00Z"},
        }]
        load_settings(self.conn, "negative_targets", rows, "2026-11-20T12:00:00+00:00")
        target = self.conn.execute("SELECT * FROM dim_target WHERE target_id='77'").fetchone()
        self.assertEqual(target["is_negative"], 1)
        self.assertIsNone(target["bid"])
        self.assertEqual(target["negative_level"], "AD_GROUP")

    def test_keyword_without_own_bid_inherits_group_default_from_db(self):
        # Снимок групп лежит в базе с прошлой загрузки, а ключи приходят отдельным файлом.
        load_settings(self.conn, "adgroups", [{"adGroupId": "2", "campaignId": "1", "name": "G",
                                              "state": "ENABLED", "defaultBid": 0.6}],
                      "2026-11-20T12:00:00+00:00", emit_creates=False)
        load_settings(self.conn, "keywords", [{"keywordId": "k1", "adGroupId": "2", "campaignId": "1",
                                              "keywordText": "крем", "matchType": "EXACT", "state": "ENABLED"}],
                      "2026-11-20T12:00:00+00:00", emit_creates=False)
        row = self.conn.execute("SELECT * FROM dim_target WHERE target_id='k1'").fetchone()
        self.assertIsNone(row["bid"])
        self.assertEqual(row["bid_source"], "AD_GROUP_DEFAULT")
        self.assertAlmostEqual(row["effective_bid"], 0.6)

    def test_history_events_import(self):
        events = [{
            "entityType": "CAMPAIGN", "entityId": "1", "changeType": "BUDGET_AMOUNT",
            "previousValue": 5, "newValue": 8, "timestamp": 1763000000000,
            "metadata": {"campaignId": "1"},
        }]
        self.assertEqual(import_changes(self.conn, events), 1)
        change = self.conn.execute("SELECT * FROM changes").fetchone()
        self.assertEqual(change["field"], "BUDGET_AMOUNT")
        self.assertEqual(change["source"], "AMAZON_HISTORY")
        self.assertEqual(change["date_precision"], "EXACT_TS")


if __name__ == "__main__":
    unittest.main()
