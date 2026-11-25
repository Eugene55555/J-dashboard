"""Покрытие данных: сколько дней реально загружено и где внутри периода пропуски.

Только синтетические данные в памяти — ни базы, ни Amazon.
"""
import sqlite3
import unittest

from app import queries
from ingest.load import SCHEMA


class Coverage(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA.read_text())

    def fact(self, day, campaign='c1', impressions=100):
        self.db.execute(
            "INSERT INTO fact_campaign_day(date,campaign_id,impressions,clicks,spend,orders,sales,units)"
            " VALUES (?,?,?,5,1.0,1,20.0,1)", (day, campaign, impressions))

    def test_missing_ranges_variants(self):
        mr = queries.missing_ranges
        self.assertEqual(mr('2026-11-01', '2026-11-03', []),
                         [['2026-11-01', '2026-11-03']])
        self.assertEqual(mr('2026-11-01', '2026-11-03',
                            ['2026-11-01', '2026-11-02', '2026-11-03']), [])
        self.assertEqual(mr('2026-11-01', '2026-11-05',
                            ['2026-11-01', '2026-11-04', '2026-11-05']),
                         [['2026-11-02', '2026-11-03']])
        # Пропуск в начале, в середине и в конце периода — три отдельных отрезка.
        self.assertEqual(mr('2026-11-01', '2026-11-06',
                            ['2026-11-02', '2026-11-03', '2026-11-05']),
                         [['2026-11-01', '2026-11-01'],
                          ['2026-11-04', '2026-11-04'],
                          ['2026-11-06', '2026-11-06']])

    def test_meta_counts_days_with_data_not_span(self):
        for day in ('2026-11-01', '2026-11-02', '2026-11-05'):
            self.fact(day)
        meta = queries.meta(self.db)
        self.assertEqual(meta['date_min'], '2026-11-01')
        self.assertEqual(meta['date_max'], '2026-11-05')  # диапазон — 5 дней
        self.assertEqual(meta['coverage_days'], 3)        # данных — за 3 дня

    def test_overview_reports_gaps_and_keeps_metrics(self):
        for day in ('2026-11-01', '2026-11-02', '2026-11-05'):
            self.fact(day)
        data = queries.overview(self.db, '2026-11-01', '2026-11-05', None)
        self.assertEqual(data['coverage']['days'], 3)
        self.assertEqual(data['coverage']['missing'], [['2026-11-03', '2026-11-04']])
        # Метрики считаются по загруженным строкам и не меняются от новой информации.
        self.assertEqual(data['current']['impressions'], 300)
        self.assertEqual(data['period']['days'], 5)

    def test_coverage_is_account_wide_not_filter_scoped(self):
        self.fact('2026-11-01', 'c1')
        self.fact('2026-11-02', 'c2')
        data = queries.overview(self.db, '2026-11-01', '2026-11-03', ['c1'])
        # Выбрана одна кампания, но период покрыт двумя днями из трёх.
        self.assertEqual(data['coverage']['days'], 2)
        self.assertEqual(data['coverage']['missing'], [['2026-11-03', '2026-11-03']])
        self.assertEqual(data['current']['impressions'], 100)

    def test_previous_window_gaps_are_reported(self):
        # Текущее окно 03–09.11 полное; в прошлом 27.10–02.11 нет 31.10.
        for day in ('2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30',
                    '2026-11-01', '2026-11-02',
                    '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06',
                    '2026-11-07', '2026-11-08', '2026-11-09'):
            self.fact(day)
        data = queries.overview(self.db, '2026-11-03', '2026-11-09', None)
        self.assertEqual(data['period']['previous'],
                         {'start': '2026-10-27', 'end': '2026-11-02'})
        self.assertEqual(data['previous_coverage']['days'], 6)
        self.assertEqual(data['previous_coverage']['missing'],
                         [['2026-10-31', '2026-10-31']])
        self.assertEqual(data['coverage']['missing'], [])

    def test_previous_window_without_data_reports_zero_days(self):
        for day in ('2026-11-03', '2026-11-04'):
            self.fact(day)
        data = queries.overview(self.db, '2026-11-03', '2026-11-04', None)
        # Окно сравнения пустое целиком: интерфейс не должен звать это «неполным».
        self.assertEqual(data['previous_coverage']['days'], 0)
        self.assertEqual(len(data['previous_coverage']['missing']), 1)
        self.assertEqual(data['current']['impressions'], 200)


if __name__ == '__main__':
    unittest.main()
