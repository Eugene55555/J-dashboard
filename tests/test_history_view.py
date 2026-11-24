import unittest
import tempfile
from pathlib import Path
from app import queries
from ingest.load import connect


class HistoryReadTest(unittest.TestCase):
    def test_no_silent_cap_and_campaign_distinct_from_object(self):
        with tempfile.TemporaryDirectory() as d:
            db = connect(Path(d) / 'test.db')
            self.addCleanup(db.close)
            db.execute("INSERT INTO dim_campaign(campaign_id,name) VALUES('one','Blue')")
            db.executemany("INSERT INTO changes(change_id,change_date,change_ts,entity_type,entity_id,campaign_id,field) VALUES(?, '2026-01-01','2026-01-01T00:00:00Z','KEYWORD','kw','one','BID')", [(str(i),) for i in range(2001)])
            rows = queries.changes(db, '2026-01-01', '2026-01-02', ['one'])
            self.assertEqual(len(rows), 2001)
            self.assertEqual(rows[0]['campaign_name'], 'Blue')
            self.assertEqual(rows[0]['entity_label'], 'kw')
