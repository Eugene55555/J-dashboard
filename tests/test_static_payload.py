"""Сборка статики: дата добавления объекта и кампания в журнале доезжают до сайта.

Без этих полей интерфейс честно показывал прочерки («дата не пришла»), хотя сама
база их знает — здесь закреплено, что сборка их не теряет.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_static
from app.config import Account
from ingest.load import connect


class BuildPayloadTest(unittest.TestCase):
    def test_build_creates_folder_and_ships_object_dates_and_campaign_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = connect(root / 'account.db')
            self.addCleanup(db.close)
            db.execute("INSERT INTO dim_campaign(campaign_id,name,state,budget) VALUES ('c1','Blue','ENABLED',5)")
            db.execute("""INSERT INTO dim_adgroup(ad_group_id,campaign_id,name,default_bid) VALUES ('g1','c1','Group',0.5)""")
            db.execute("""INSERT INTO dim_target(target_id,campaign_id,ad_group_id,kind,targeting,state,
                                                     bid,bid_source,created_at)
                          VALUES ('k1','c1','g1','KEYWORD','крем','ENABLED',0.2,'OWN','2026-10-01T09:00:00Z')""")
            db.execute("""INSERT INTO fact_campaign_day(date,campaign_id,impressions,clicks,spend,orders,sales,units)
                          VALUES ('2026-11-20','c1',10,1,0.5,0,0,0)""")
            db.execute("""INSERT INTO fact_target_day(date,campaign_id,ad_group_id,target_id,impressions,clicks,spend,
                                                     orders,sales,units)
                          VALUES ('2026-11-20','c1','g1','k1',10,1,0.5,0,0,0)""")
            db.execute("""INSERT INTO changes(change_id,change_date,change_ts,date_precision,entity_type,entity_id,
                                                     campaign_id,field,old_value,new_value,source)
                          VALUES ('x','2026-11-21','2026-11-21T08:00:00+00:00','EXACT_TS','KEYWORD','k1','c1',
                                  'STATUS','ENABLED','PAUSED','AMAZON_HISTORY')""")
            db.commit()

            account = Account(id='probe', label='Probe', db_path=root / 'account.db')
            # Каталога ещё нет: сборка обязана создать его сама, иначе первая запись падает.
            out = root / 'nested' / 'site'
            with patch.object(build_static, 'connect', return_value=db), \
                 patch.object(build_static.settings.__class__, 'accounts', return_value={'probe': account}):
                result = build_static.build('probe', out)

            data = json.loads((out / 'data.json').read_text(encoding='utf-8'))
            self.assertEqual(result['changes'], 1)
            self.assertEqual(data['targets'][0]['created_at'], '2026-10-01T09:00:00Z')
            self.assertEqual(data['targets'][0]['kind'], 'KEYWORD')
            self.assertEqual(data['changes'][0]['campaign_name'], 'Blue')
            self.assertTrue((out / 'static' / 'app.js').is_file())
            self.assertTrue((out / 'index.html').is_file())


if __name__ == '__main__':
    unittest.main()