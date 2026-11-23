"""Выгрузка отчётов и настроек Amazon Ads API в папку inbox.

Запускать на том хосте, где лежит secrets.env нужного аккаунта:

    python -m ingest.amazon --days 7 --out data/inbox
    python -m ingest.amazon --start 2026-11-01 --end 2026-11-23

Каждый файл пишется отдельно и сразу пригоден для загрузки: имя файла
содержит дату запуска, содержимое — сырой ответ API.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://advertising-api.amazon.com"
DEFAULT_ACCOUNT = ""  # если не задан — находится по profile_id через /adsAccounts/list
DEFAULT_KEYFILE = Path.home() / "hermes-workspace/ads-amazon-key/secrets.env"

METRICS = [
    "metric.impressions", "metric.clicks", "metric.totalCost",
    "metric.purchases", "metric.sales", "metric.unitsSold",
]
BASE_FIELDS = ["date.value", "budgetCurrency.value", "campaign.id", "campaign.name"]
DETAIL_FIELDS = [
    "adGroup.id", "adGroup.name", "ad.id", "advertisedProduct.id", "advertisedProduct.name",
    "advertisedProductMarketplace.value", "adGroupTarget.id", "targeting.value",
    "targetingMatchType.value", "searchTerm.value", "placementClassification.value",
]

LISTS = [
    ("campaigns", "/sp/campaigns/list", "application/vnd.spCampaign.v3+json", "campaigns"),
    ("adgroups", "/sp/adGroups/list", "application/vnd.spAdGroup.v3+json", "adGroups"),
    ("productads", "/sp/productAds/list", "application/vnd.spProductAd.v3+json", "productAds"),
    ("targets", "/sp/targets/list", "application/vnd.spTargetingClause.v3+json", "targetingClauses"),
    ("keywords", "/sp/keywords/list", "application/vnd.spKeyword.v3+json", "keywords"),
    ("negative_targets", "/sp/negativeTargets/list", "application/vnd.spNegativeTargetingClause.v3+json", "negativeTargetingClauses"),
    ("campaign_negative_targets", "/sp/campaignNegativeTargets/list", "application/vnd.spCampaignNegativeTargetingClause.v3+json", "campaignNegativeTargetingClauses"),
    ("negative_keywords", "/sp/negativeKeywords/list", "application/vnd.spNegativeKeyword.v3+json", "negativeKeywords"),
    ("campaign_negative_keywords", "/sp/campaignNegativeKeywords/list", "application/vnd.spCampaignNegativeKeyword.v3+json", "campaignNegativeKeywords"),
]


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def token(env: dict[str, str]) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": env["refresh_token"],
        "client_id": env["client_id"],
        "client_secret": env["client_secret"],
    }).encode()
    request = urllib.request.Request("https://api.amazon.com/auth/o2/token", data=body)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["access_token"]


def api(method: str, path: str, payload=None, media=None, tries: int = 5) -> tuple[int, dict]:
    headers = {
        "Authorization": "Bearer " + TOKEN,
        "Amazon-Advertising-API-ClientId": CLIENT_ID,
        "Amazon-Advertising-API-Scope": PROFILE,
        "Accept": media or "application/json",
        "Content-Type": media or "application/json",
    }
    body = json.dumps(payload).encode() if payload is not None else None
    last: tuple[int, dict] = (0, {})
    for attempt in range(tries):
        request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as err:
            text = err.read().decode(errors="ignore")
            try:
                payload_out = json.loads(text)
            except ValueError:
                payload_out = {"raw": text[:500]}
            last = (err.code, payload_out)
            if err.code in (429, 500, 502, 503, 504):
                time.sleep(min(90, 2 ** (attempt + 2)))
                continue
            return last
    return last


def fetch_report(name: str, fields: list[str], start: str, end: str, out: Path) -> int:
    payload = {
        "accessRequestedAccounts": [{"advertiserAccountId": ACCOUNT}],
        "reports": [{
            "format": "CSV",
            "periods": [{"datePeriod": {"startDate": start, "endDate": end}}],
            "query": {"fields": fields},
        }],
    }
    status, body = api("POST", "/adsApi/v1/create/reports", payload)
    if status not in (200, 207) or not body.get("success"):
        raise SystemExit(f"{name}: create failed HTTP {status}: {json.dumps(body)[:500]}")
    report_id = body["success"][0]["report"]["reportId"]
    print(f"{name}: report {report_id}", flush=True)
    for cycle in range(90):
        status, body = api("POST", "/adsApi/v1/retrieve/reports", {"reportIds": [report_id]})
        if status not in (200, 207):
            raise SystemExit(f"{name}: retrieve failed HTTP {status}: {json.dumps(body)[:500]}")
        report = (body.get("success") or [{}])[0].get("report") or {}
        state = report.get("status")
        if state == "COMPLETED":
            rows = []
            for part in report.get("completedReportParts") or []:
                with urllib.request.urlopen(part["url"], timeout=180) as download:
                    raw = download.read()
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass

                rows.extend(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            target = out / f"{name}_{start}_{end}.json"
            target.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print(f"{name}: {len(rows)} строк → {target.name}", flush=True)
            return len(rows)
        if state in ("FAILED", "CANCELLED"):
            raise SystemExit(f"{name}: {state}: {json.dumps(report)[:500]}")
        print(f"{name}: poll {cycle + 1} · {state}", flush=True)
        time.sleep(60)
    raise SystemExit(f"{name}: таймаут ожидания отчёта")


def fetch_lists(out: Path) -> None:
    for name, path, media, key in LISTS:
        rows, token_value = [], None
        while True:
            payload = {"stateFilter": {"include": ["ENABLED", "PAUSED", "ARCHIVED"]},
                       "includeExtendedDataFields": True, "maxResults": 1000}
            if token_value:
                payload["nextToken"] = token_value
            status, body = api("POST", path, payload, media)
            if status != 200 or key not in body:
                raise SystemExit(f"{name}: HTTP {status}: {json.dumps(body)[:400]}")
            rows.extend(body.get(key) or [])
            token_value = body.get("nextToken")
            if not token_value:
                break
            time.sleep(0.4)
        (out / f"settings_{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"{name}: {len(rows)} объектов", flush=True)


def main() -> None:
    global TOKEN, CLIENT_ID, PROFILE, ACCOUNT
    parser = argparse.ArgumentParser(description="Выгрузка Amazon Ads в папку inbox")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out", default="data/inbox")
    parser.add_argument("--keys", default=str(DEFAULT_KEYFILE))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--account", default=DEFAULT_ACCOUNT,
                        help="advertiserAccountId; по умолчанию ищется по profile_id")
    parser.add_argument("--settings-only", action="store_true")
    args = parser.parse_args()

    env = read_env(Path(args.keys))
    PROFILE = args.profile or env.get("profile_id_us") or ""
    CLIENT_ID = env["client_id"]
    ACCOUNT = args.account
    if not PROFILE:
        raise SystemExit("в secrets.env нет profile_id_us — укажите --profile")

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=args.days - 1)).isoformat()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    TOKEN = token(env)
    status, profile = api("GET", "/v2/profiles")
    if status != 200:
        raise SystemExit(f"/v2/profiles: HTTP {status}")
    current = next((p for p in profile if str(p.get("profileId")) == PROFILE), None)
    if not current:
        raise SystemExit(f"профиль {PROFILE} недоступен этим ключом")
    (out / "account.json").write_text(json.dumps({"profile": current, "window": [start, end],
                                                  "pulled_at": datetime.now(timezone.utc).isoformat()},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"profile {PROFILE} · {current.get('currencyCode')} · {start}..{end}", flush=True)

    if not ACCOUNT:
        status, mapping = api("POST", "/adsAccounts/list", {})
        if status != 200:
            raise SystemExit(f"/adsAccounts/list: HTTP {status}")
        found = None
        for item in mapping.get("adsAccounts", []):
            for alternate in item.get("alternateIds", []) or []:
                if str(alternate.get("profileId")) == PROFILE:
                    found = item.get("adsAccountId")
                    break
            if found:
                break
        if not found:
            raise SystemExit(f"не найден advertiserAccountId для профиля {PROFILE}")
        ACCOUNT = found
    print(f"advertiserAccountId {ACCOUNT}", flush=True)

    if not args.settings_only:
        fetch_report("campaign_window", BASE_FIELDS + METRICS, start, end, out)
        fetch_report("detail_window", BASE_FIELDS + DETAIL_FIELDS + METRICS, start, end, out)
    fetch_lists(out)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
