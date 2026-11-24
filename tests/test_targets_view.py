"""Вкладка «Ключи и цели» и журнал: дата добавления, события, отсутствие тихих лимитов.

Только синтетические данные в памяти — ни базы, ни Amazon.
"""
import sqlite3
import unittest

from app import queries
from ingest.load import SCHEMA, _history_label

DAY = "2026-11-20"


class Base(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA.read_text())
        self.db.execute("INSERT INTO dim_campaign(campaign_id,name,state) VALUES ('c1','Blue','ENABLED')")
        self.db.execute("INSERT INTO dim_adgroup(ad_group_id,campaign_id,name,default_bid,created_at)"
                        " VALUES ('g1','c1','Group',0.75,'2026-09-25T10:00:00Z')")

    def target(self, tid, bid=None, state='ENABLED', targeting=None, kind='KEYWORD'):
        self.db.execute("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,
                              match_type,state,bid,bid_source,effective_bid,created_at)
                           VALUES (?,'c1','g1',?,?, 'EXACT',?,?,?,?, '2026-10-01T09:00:00Z')""",
                        (tid, kind, targeting or f'Text {tid}', state, bid,
                         'OWN' if bid is not None else 'AD_GROUP_DEFAULT', bid if bid is not None else 0.75))

    def fact(self, tid, impressions=0, spend=0.0):
        self.db.execute("""INSERT INTO fact_target_day(date,campaign_id,ad_group_id,target_id,
                              impressions,clicks,spend,orders,sales,units)
                           VALUES (?, 'c1','g1',?,?,?,?,0,0,0)""",
                        (DAY, tid, impressions, 1 if impressions else 0, spend))

    def change(self, cid, tid, field, old, new, ts, entity='KEYWORD'):
        self.db.execute("""INSERT INTO changes(change_id,entity_id,entity_type,field,old_value,
                              new_value,campaign_id,change_date,change_ts,date_precision,source)
                           VALUES (?,?,?,?,?,?,'c1',?,?,'EXACT_TS','AMAZON_HISTORY')""",
                        (cid, tid, entity, field, old, new, ts[:10], ts))

    def rows(self, **kw):
        return queries.targets(self.db, '2026-11-01', '2026-11-30', ['c1'], **kw)


class TargetViewTest(Base):
    def test_idle_objects_are_flagged_and_filterable(self):
        self.target('k1', bid=0.3)
        self.target('k2', bid=0.4)
        self.fact('k1', impressions=50, spend=1.2)
        by_id = {r['target_id']: r for r in self.rows()}
        self.assertEqual(by_id['k1']['idle'], 0)
        self.assertEqual(by_id['k2']['idle'], 1, 'объект без показов помечен, а не выброшен из списка')
        self.assertEqual([r['target_id'] for r in self.rows(only_idle=True)], ['k2'])

    def test_every_object_is_returned_without_silent_limit(self):
        self.db.executemany("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,
                                  state,bid,bid_source) VALUES (?,'c1','g1','KEYWORD',?,'ENABLED',0.2,'OWN')""",
                            [(f'k{i}', f'Text {i}') for i in range(2500)])
        self.assertEqual(len(self.rows()), 2500)

    def test_latest_change_is_shown_per_object(self):
        self.target('k1', bid=0.2)
        self.change('a', 'k1', 'BID_AMOUNT', '0.4', '0.2', '2026-11-18T14:35:54+00:00')
        self.change('b', 'k1', 'STATUS', 'ENABLED', 'PAUSED', '2026-11-21T08:00:00+00:00')
        row = self.rows()[0]
        self.assertEqual((row['last_field'], row['last_old'], row['last_new']), ('STATUS', 'ENABLED', 'PAUSED'))
        self.assertEqual(row['last_date'], '2026-11-21')

    def test_object_without_events_has_empty_last_change(self):
        self.target('k1', bid=0.2)
        row = self.rows()[0]
        self.assertIsNone(row['last_field'])
        self.assertEqual(row['created_at'][:10], '2026-10-01', 'дата добавления берётся из снимка настроек')


class HistoryLabelTest(unittest.TestCase):
    def test_keyword_event_is_labelled_by_text_not_id(self):
        label = _history_label('KEYWORD', 'kw-1', {'keyword': 'крем для рук', 'keywordType': 'KEYWORD_EXACT'})
        self.assertEqual(label, 'крем для рук · точное')

    def test_product_targeting_uses_expression(self):
        self.assertEqual(_history_label('PRODUCT_TARGETING', 't-1', {'targetingExpression': 'asin="B0TEST"'}),
                         'asin="B0TEST"')
        self.assertEqual(_history_label('PRODUCT_TARGETING', 't-2', {'predefinedTarget': 'CLOSE-MATCH'}),
                         'авто · близкие запросы')
        # Amazon отдаёт авто-таргеты и как QUERY_HIGH_REL_MATCHES: в журнале это тоже текст, не код.
        self.assertEqual(_history_label('PRODUCT_TARGETING', 't-3',
                                        {'predefinedTarget': 'QUERY_HIGH_REL_MATCHES'}),
                         'авто · близкие запросы')
        self.assertEqual(_history_label('PRODUCT_TARGETING', 't-4',
                                        {'targetingExpression': 'ASIN_SAME_AS:B0HKFFNS88'}),
                         'авто · тот же товар · B0HKFFNS88')

    def test_unknown_shape_falls_back_to_id(self):
        self.assertEqual(_history_label('AD', 'ad-1', {}), 'ad-1')


class JournalFilterTest(Base):
    def test_field_group_and_entity_filters(self):
        self.target('k1', bid=0.2)
        self.change('a', 'k1', 'BID_AMOUNT', '0.4', '0.2', '2026-11-18T14:35:54+00:00')
        self.change('b', 'k1', 'STATUS', 'ENABLED', 'PAUSED', '2026-11-21T08:00:00+00:00')
        self.change('c', 'c1', 'BUDGET_AMOUNT', '5', '9', '2026-11-21T09:00:00+00:00', entity='CAMPAIGN')
        self.assertEqual([r['change_id'] for r in queries.changes(self.db, '2026-11-01', '2026-11-30',
                                                                  ['c1'], field_group='status')], ['b'])
        self.assertEqual([r['change_id'] for r in queries.changes(self.db, '2026-11-01', '2026-11-30',
                                                                  ['c1'], field_group='budget')], ['c'])
        self.assertEqual([r['change_id'] for r in queries.changes(self.db, '2026-11-01', '2026-11-30',
                                                                  ['c1'], field_group='bid')], ['a'])
        self.assertEqual([r['change_id'] for r in queries.changes(self.db, '2026-11-01', '2026-11-30',
                                                                  ['c1'],
                                                                  entity_type=['KEYWORD'])], ['b', 'a'])

    def test_unknown_field_group_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            queries.changes(self.db, '2026-11-01', '2026-11-30', ['c1'], field_group='unknown')

    def test_campaign_scope_uses_only_the_events_own_campaign(self):
        self.target('k1', bid=0.2)
        self.change('a', 'k1', 'BID_AMOUNT', '0.4', '0.2', '2026-11-18T14:35:54+00:00')
        # Событие с чужим кампанейским ID в записи не должно попасть в выборку кампании c1.
        self.db.execute("""INSERT INTO changes(change_id,entity_id,entity_type,field,campaign_id,
                              change_date,change_ts,source) VALUES ('z','k1','KEYWORD','STATUS','c2',
                              '2026-11-21','2026-11-21T08:00:00+00:00','AMAZON_HISTORY')""")
        rows = queries.changes(self.db, '2026-11-01', '2026-11-30', ['c1'])
        self.assertEqual([r['change_id'] for r in rows], ['a'])
        self.assertEqual(rows[0]['campaign_name'], 'Blue')
        self.assertEqual(rows[0]['entity_label'], 'Text k1')


if __name__ == '__main__':
    unittest.main()


class InheritedBidTest(Base):
    def test_object_without_own_bid_shows_group_bid(self):
        self.db.execute("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,state,bid,
                              bid_source) VALUES ('k9','c1','g1','KEYWORD','маска','PAUSED',NULL,'UNKNOWN')""")
        row = self.rows()[0]
        self.assertIsNone(row['bid'], 'своей ставки у объекта нет')
        self.assertEqual(row['effective_bid'], 0.75, 'показывается ставка группы')
        self.assertEqual(row['bid_source'], 'AD_GROUP_DEFAULT')

    def test_negative_never_gets_a_bid(self):
        self.db.execute("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,state,is_negative,
                              negative_level) VALUES ('n1','c1','g1','PRODUCT','asin="B0"','ENABLED',1,'AD_GROUP')""")
        row = [r for r in self.rows(kind='negative') if r['target_id'] == 'n1'][0]
        self.assertIsNone(row['bid'])
        self.assertIsNone(row['effective_bid'], 'у негатива ставки не бывает')


class TargetingLabelTest(unittest.TestCase):
    def test_auto_codes_are_translated_and_keywords_kept(self):
        self.assertEqual(queries.targeting_label('QUERY_HIGH_REL_MATCHES'), 'авто · близкие запросы')
        self.assertEqual(queries.targeting_label('ASIN_SAME_AS:B0HKFFNS88'), 'авто · тот же товар · B0HKFFNS88')
        self.assertEqual(queries.targeting_label('крем для рук'), 'крем для рук')
        self.assertEqual(queries.targeting_label(''), '')

    def test_targets_view_keeps_raw_value_for_tooltip(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA.read_text())
        db.execute("INSERT INTO dim_campaign(campaign_id,name) VALUES ('c1','Blue')")
        db.execute("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,state,bid)
                      VALUES ('t1','c1','g1','AUTO','QUERY_HIGH_REL_MATCHES','ENABLED',0.2)""")
        row = queries.targets(db, '2026-11-01', '2026-11-30', ['c1'])[0]
        self.assertEqual(row['targeting'], 'авто · близкие запросы')
        self.assertEqual(row['targeting_raw'], 'QUERY_HIGH_REL_MATCHES')


class JournalGroupTest(Base):
    def test_journal_shows_group_name_not_id(self):
        self.target('k1', bid=0.2)
        self.db.execute("""INSERT INTO changes(change_id,entity_id,entity_type,field,campaign_id,ad_group_id,
                              change_date,change_ts,source)
                           VALUES ('g','k1','KEYWORD','BID_AMOUNT','c1','g1','2026-11-21',
                                   '2026-11-21T08:00:00+00:00','AMAZON_HISTORY')""")
        row = queries.changes(self.db, '2026-11-01', '2026-11-30', ['c1'])[0]
        self.assertEqual(row['ad_group_name'], 'Group')
        self.assertEqual(row['campaign_name'], 'Blue')
