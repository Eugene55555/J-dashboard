"""Ограничение попыток входа: счётчик неудач и ответ 429 с Retry-After.

Пароль в тестах синтетический, сеть не используется (TestClient в процессе).
"""
import unittest

from fastapi.testclient import TestClient

from app import main
from app.config import Settings

GOOD = 'synthetic-test-password'


class ThrottleUnit(unittest.TestCase):
    def test_blocks_after_limit_and_releases(self):
        t = main.LoginThrottle(limit=3, window=60, block=60)
        now = 1000.0
        self.assertEqual(t.check('ip', now), 0)
        self.assertEqual(t.fail('ip', now), 0)
        self.assertEqual(t.fail('ip', now), 0)
        blocked = t.fail('ip', now)                      # третья неудача — блокировка
        self.assertEqual(blocked, 60)
        self.assertEqual(t.check('ip', now + 10), 50)
        self.assertEqual(t.check('ip', now + 61), 0)     # блокировка истекла
        self.assertEqual(t.check('other', now), 0)       # чужой ключ не задет

    def test_old_failures_fall_out_of_window(self):
        t = main.LoginThrottle(limit=3, window=60, block=60)
        t.fail('ip', 1000.0)
        t.fail('ip', 1030.0)
        # обе неудачи старше окна — лимит не достигается
        self.assertEqual(t.fail('ip', 1100.0), 0)

    def test_reset_clears_counter(self):
        t = main.LoginThrottle(limit=2, window=60, block=60)
        t.fail('ip', 1000.0)
        t.fail('ip', 1000.0)
        self.assertGreater(t.check('ip', 1000.0), 0)
        t.reset('ip')
        self.assertEqual(t.check('ip', 1000.0), 0)


class LoginEndpoint(unittest.TestCase):
    def setUp(self):
        self._settings = main.settings
        self._throttle = main.LOGIN_THROTTLE
        main.settings = Settings(password=GOOD, secret='test-secret')
        main.LOGIN_THROTTLE = main.LoginThrottle(limit=3, window=60, block=60)
        self.client = TestClient(main.app)

    def tearDown(self):
        main.settings = self._settings
        main.LOGIN_THROTTLE = self._throttle

    def test_third_failure_locks_and_returns_retry_after(self):
        for _ in range(2):
            r = self.client.post('/api/login', json={'password': 'nope'})
            self.assertEqual(r.status_code, 403)
            self.assertNotIn('Retry-After', r.headers)
        r = self.client.post('/api/login', json={'password': 'nope'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('Retry-After', r.headers)

        # Даже верный пароль во время паузы получает 429, а не сессию.
        r = self.client.post('/api/login', json={'password': GOOD})
        self.assertEqual(r.status_code, 429)
        self.assertGreater(int(r.headers['Retry-After']), 0)
        self.assertNotIn(main.COOKIE, r.cookies)

    def test_success_sets_cookie_and_clears_counter(self):
        self.assertEqual(
            self.client.post('/api/login', json={'password': 'nope'}).status_code, 403)
        r = self.client.post('/api/login', json={'password': GOOD})
        self.assertEqual(r.status_code, 200)
        self.assertIn(main.COOKIE, r.cookies)
        self.assertEqual(main.LOGIN_THROTTLE.check('testclient'), 0)

    def test_no_password_configured_allows_login(self):
        main.settings = Settings(password='', secret='test-secret')
        r = self.client.post('/api/login', json={'password': 'anything'})
        self.assertEqual(r.status_code, 200)


if __name__ == '__main__':
    unittest.main()