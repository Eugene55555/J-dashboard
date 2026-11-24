"""Контракт выгрузки истории Amazon: батчи, страницы, токен, прогресс.

Сеть подменяется фейковым транспортом — тесты не ходят в Amazon и не требуют
ключей: проверяется форма запроса и поведение при неполном ответе.
"""
import json
import tempfile
import unittest
from pathlib import Path

from ingest import history


def page(events, total, token=None, offset=0, size=200, max_page=1):
    return {"events": events, "totalRecords": total, "pageSize": size,
            "pageOffset": offset, "maxPageNumber": max_page, "nextToken": token}


class FakeNet:
    """Отвечает по правилам: токен, кампании, страницы /history."""

    def __init__(self, pages=None, token_status=200, expired=0):
        self.pages = list(pages or [])
        self.token_status = token_status
        self.expired = expired        # сколько первых вызовов /history отдать 401
        self.requests = []
        self.tokens_issued = 0

    def __call__(self, method, url, headers, body, timeout=120):
        parsed = None
        if body and body[:1] in (b"{", b"["):
            parsed = json.loads(body)
        self.requests.append({"url": url, "headers": headers, "body": parsed})
        if url == history.TOKEN_URL:
            if self.token_status != 200:
                return self.token_status, {"error": "invalid_client"}
            self.tokens_issued += 1
            return 200, {"access_token": f"Atza|{self.tokens_issued}"}
        if url.endswith("/sp/campaigns/list"):
            return 200, {"campaigns": [{"campaignId": "1"}, {"campaignId": "2"}]}
        if url.endswith("/history"):
            if self.expired:
                self.expired -= 1
                return 401, {"message": "token expired"}
            if not self.pages:
                raise AssertionError("лишний запрос /history — страницы кончились")
            item = self.pages.pop(0)
            return item if isinstance(item, tuple) else (200, item)
        raise AssertionError(f"неожиданный запрос: {url}")


def requester(net):
    env = {"client_id": "cid", "client_secret": "sec", "refresh_token": "Atzr|r"}
    return history.Requester(env, "2346284607821433", pause=1, transport=net,
                             sleep=lambda seconds: None, log=lambda *a, **k: None)


class PayloadTest(unittest.TestCase):
    def test_campaigns_use_ids_and_children_use_parents(self):
        payload = history.event_types_payload(["1", "2"])
        self.assertEqual(payload["CAMPAIGN"]["eventTypeIds"], ["1", "2"])
        for kind in ("KEYWORD", "PRODUCT_TARGETING", "NEGATIVE_KEYWORD", "AD_GROUP", "AD"):
            self.assertEqual(payload[kind]["parents"],
                             [{"campaignId": "1", "useProfileIdAdvertiser": True},
                              {"campaignId": "2", "useProfileIdAdvertiser": True}],
                             f"{kind}: события детей ищутся по родителю, а не по ID объекта")

    def test_more_than_ten_campaigns_is_rejected(self):
        with self.assertRaises(history.HistoryError):
            history.event_types_payload([str(i) for i in range(11)])

    def test_request_sets_window_count_and_sort_direction(self):
        body = history.history_request(["1"], 1000, 2000)
        self.assertEqual((body["fromDate"], body["toDate"]), (1000, 2000))
        self.assertEqual(body["sort"]["direction"], "DESC")
        self.assertEqual(history.history_request(["1"], 1000, 2000, count=500)["count"], 200)

    def test_filters_are_sent_per_entity_and_exclude_in_budget(self):
        payload = history.event_types_payload(["1"])
        self.assertEqual(payload["KEYWORD"]["filters"], ["STATUS", "BID_AMOUNT"])
        self.assertNotIn("IN_BUDGET", payload["CAMPAIGN"]["filters"],
                         "«вошла/вышла из бюджета» — шум, который забивает страницы")
        self.assertIn("PLACEMENT_GROUP", payload["CAMPAIGN"]["filters"])
        self.assertEqual(payload["NEGATIVE_KEYWORD"]["filters"], ["STATUS"])
        # Каждый тип берёт только те типы изменений, которые сервис ему разрешает.
        documented = {"BID_AMOUNT", "BUDGET_AMOUNT", "END_DATE", "IN_BUDGET", "NAME",
                      "PLACEMENT_GROUP", "SMART_BIDDING_STRATEGY", "START_DATE", "STATUS"}
        for kind, allowed in history.HISTORY_FILTERS.items():
            self.assertTrue(allowed, f"{kind}: фильтры не заданы")
            self.assertLessEqual(set(allowed), documented, f"{kind}: недопустимый тип изменения")
            self.assertEqual(allowed, tuple(payload[kind]["filters"]))
        self.assertNotIn("filters", history.event_types_payload(["1"], filters=False)["KEYWORD"])


class PullTest(unittest.TestCase):
    def test_walks_every_page_and_reaches_total(self):
        net = FakeNet(pages=[page([{"entityId": "a"}], 2, token="t1", max_page=2),
                             page([{"entityId": "b"}], 2, offset=200, max_page=2)])
        events = history.pull_batch(requester(net), ["1", "2"], 0, 1, pause=0)
        self.assertEqual([e["entityId"] for e in events], ["a", "b"])
        self.assertEqual(net.requests[-1]["headers"]["Amazon-Advertising-API-Scope"],
                         "2346284607821433")

    def test_short_answer_is_an_error_not_a_silent_success(self):
        net = FakeNet(pages=[page([{"entityId": "a"}], 5, max_page=1)])
        with self.assertRaises(history.HistoryError):
            history.pull_batch(requester(net), ["1"], 0, 1, pause=0)

    def test_batch_splits_ten_campaigns_per_request(self):
        net = FakeNet(pages=[page([], 0), page([], 0)])
        request = requester(net)
        ids = [str(i) for i in range(11)]
        history.batches(ids) and [history.pull_batch(request, chunk, 0, 1, pause=0)
                                  for chunk in history.batches(ids)]
        sent = [r["body"]["eventTypes"]["CAMPAIGN"]["eventTypeIds"]
                for r in net.requests if r["url"].endswith("/history")]
        self.assertEqual(sent, [[str(i) for i in range(10)], ["10"]])

    def test_expired_token_is_refreshed_once(self):
        net = FakeNet(pages=[page([{"entityId": "a"}, {"entityId": "b"}], 2)], expired=1)
        events = history.pull_batch(requester(net), ["1"], 0, 1, pause=0)
        self.assertEqual(len(events), 2)
        self.assertEqual(net.tokens_issued, 2, "просроченный токен обновляется, запрос повторяется")

    def test_bad_keys_stop_the_pull(self):
        net = FakeNet(token_status=401)
        with self.assertRaises(history.HistoryError):
            requester(net).token()


class ProgressTest(unittest.TestCase):
    def test_second_run_resumes_without_refetching_finished_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            net = FakeNet(pages=[page([{"entityId": "x"}], 1), page([{"entityId": "y"}], 1)])
            request = requester(net)
            ids = [str(i) for i in range(11)]        # два батча: 10 + 1
            first = history.pull(ids, 0, 5000, out, request, pause=0, profile="p")
            self.assertEqual(len(first["events"]), 2)
            self.assertTrue(first["progress"]["complete"])
            calls_after_first = len(net.requests)

            net.pages = []
            second = history.pull(ids, 0, 5000, out, request, pause=0, profile="p")
            self.assertEqual(len(second["events"]), 2, "собранное не теряется при повторном запуске")
            self.assertEqual(len(net.requests), calls_after_first, "готовые батчи не перезапрашиваются")

    def test_changed_window_starts_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            net = FakeNet(pages=[page([{"entityId": "x"}], 1)])
            history.pull(["1"], 0, 5000, out, requester(net), pause=0, profile="p")
            net.pages = [page([{"entityId": "y"}], 1)]
            result = history.pull(["1"], 0, 9000, out, requester(net), pause=0, profile="p")
            self.assertEqual([e["entityId"] for e in result["events"]], ["y"])


if __name__ == "__main__":
    unittest.main()
