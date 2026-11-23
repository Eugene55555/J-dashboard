"""Выгрузка истории изменений Amazon (POST /history) в папку inbox.

Эндпоинт жёстко троттлится: не больше 10 ID за запрос и пауза между
запросами от 180 секунд. Поэтому на 23 кампании уходят десятки минут —
запускайте в фоне (nohup) и не перезапускайте при таймауте ssh.

    python -m ingest.history --days 88 --out data/inbox
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://advertising-api.amazon.com"
DEFAULT_KEYFILE = Path.home() / "hermes-workspace/ads-amazon-key/secrets.env"


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def request(method: str, path: str, payload=None, media="application/json", tries: int = 6):
    headers = {
        "Authorization": "Bearer " + TOKEN,
        "Amazon-Advertising-API-ClientId": CLIENT_ID,
        "Amazon-Advertising-API-Scope": PROFILE,
        "Accept": media,
        "Content-Type": media if media != "application/json" else "application/json",
    }
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(tries):
        call = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(call, timeout=120) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as err:
            text = err.read().decode(errors="ignore")
            if err.code == 429:
                wait = 180 * (attempt + 1)
                print(f"429 · пауза {wait} с", flush=True)
                time.sleep(wait)
                continue
            try:
                return err.code, json.loads(text)
            except ValueError:
                return err.code, {"raw": text[:400]}
        except urllib.error.URLError:
            time.sleep(30)
    return 0, {}


def campaigns() -> list[str]:
    status, body = request(
        "POST", "/sp/campaigns/list",
        {"stateFilter": {"include": ["ENABLED", "PAUSED", "ARCHIVED"]}, "maxResults": 1000},
        "application/vnd.spCampaign.v3+json",
    )
    if status != 200:
        raise SystemExit(f"campaigns: HTTP {status}")
    return [str(c["campaignId"]) for c in body.get("campaigns", [])]


def main() -> None:
    global TOKEN, CLIENT_ID, PROFILE
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=88)
    parser.add_argument("--out", default="data/inbox")
    parser.add_argument("--keys", default=str(DEFAULT_KEYFILE))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--accept", default="application/vnd.historyresponse.v1.0+json")
    parser.add_argument("--pause", type=int, default=180)
    args = parser.parse_args()

    env = read_env(Path(args.keys))
    CLIENT_ID = env["client_id"]
    PROFILE = args.profile or env.get("profile_id_us") or ""
    token_request = urllib.request.Request(
        "https://api.amazon.com/auth/o2/token",
        data=urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": env["refresh_token"],
            "client_id": env["client_id"], "client_secret": env["client_secret"],
        }).encode(),
    )
    with urllib.request.urlopen(token_request, timeout=60) as response:
        TOKEN = json.loads(response.read())["access_token"]

    ids = campaigns()
    now = datetime.now(timezone.utc)
    # Окно 90 дней считается от часов сервиса; берём с запасом в 2 дня.
    start = now - timedelta(days=min(args.days, 88))
    payload: dict = defaultdict(list)
    total_records = 0
    for index in range(0, len(ids), 10):
        batch = ids[index:index + 10]
        offset = 0
        while True:
            status, body = request(
                "POST", "/history",
                {
                    "fromDate": int(start.timestamp() * 1000),
                    "toDate": int(now.timestamp() * 1000),
                    "eventTypes": {"CAMPAIGN": {"eventTypeIds": batch}},
                    "count": 200,
                    "pageOffset": offset,
                    "sort": {"key": "DATE", "direction": "DESC"},
                },
                args.accept,
            )
            if status != 200:
                raise SystemExit(f"history: HTTP {status} {json.dumps(body)[:300]}")
            events = body.get("events") or []
            payload["events"].extend(events)
            total_records = body.get("totalRecords", total_records)
            print(f"batch {index // 10 + 1}: +{len(events)} (всего {len(payload['events'])}/{total_records})", flush=True)
            if not body.get("nextToken") and len(payload["events"]) >= (body.get("pageOffset", 0) + len(events)):
                if not events or len(payload["events"]) >= total_records:
                    break
            offset += len(events) or 200
            if not events or len(payload["events"]) >= total_records:
                break
            time.sleep(args.pause)
        if index + 10 < len(ids):
            time.sleep(args.pause)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"history_{date.today().isoformat()}.json"
    target.write_text(json.dumps(payload["events"], ensure_ascii=False), encoding="utf-8")
    print(f"DONE {len(payload['events'])} событий → {target}", flush=True)


if __name__ == "__main__":
    main()
