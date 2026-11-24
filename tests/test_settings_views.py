"""Контракт чтения настроек и журнала. Только синтетические данные."""
import sqlite3
import unittest

from app import queries
from ingest.load import SCHEMA


class SettingsViewTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA.read_text())
        self.db.execute("INSERT INTO dim_campaign(campaign_id,name,state) VALUES ('one','Blue','ENABLED')")
        self.db.execute("INSERT INTO dim_adgroup(ad_group_id,campaign_id,name,default_bid) VALUES ('g','one','Group',0.75)")

    def target(self, tid, group='g', bid=None, negative=0, kind='KEYWORD'):
        self.db.execute('''INSERT INTO dim_target(target_id,campaign_id,ad_group_id,targeting,kind,bid,is_negative)
                           VALUES (?,'one',?,?,?,?,?)''', (tid, group, 'Text ' + tid, kind, bid, negative))

    def event(self, cid, tid, entity, field, new, day='2026-01-02', precision='DAY', label=None):
        self.db.execute('''INSERT INTO changes(change_id,entity_id,entity_type,field,new_value,campaign_id,
                            ad_group_id,change_date,date_precision,source,entity_label)
                            VALUES (?,?,?,?,?,'one','g',?,?,'IMPORT',?)''',
                        (cid, tid, entity, field, new, day, precision, label))

    def test_journal_resolves_object_and_searches_campaign_and_object(self):
        self.target('k')
        self.event('a', 'k', 'KEYWORD', 'BID', '1', label='one')
        self.event('b', 'g', 'AD_GROUP', 'DEFAULT_BID', '2')
        self.event('c', 'one', 'CAMPAIGN', 'STATUS', 'PAUSED', label='one')
        for search, entity, expected in [('blue', 'KEYWORD', 'Text k'), ('group', 'AD_GROUP', 'Group'),
                                         (None, 'CAMPAIGN', 'Blue'), ('Text k', None, 'Text k')]:
            with self.subTest(search=search):
                rows = queries.changes(self.db, '2026-01-01', '2026-01-31', ['one'], search, entity_type=entity)
                self.assertEqual([(r['campaign_name'], r['entity_label']) for r in rows], [('Blue', expected)])
