"""Выгрузка истории изменений Amazon (POST /history) в папку inbox.

Контракт — официальная схема `Changehistory_prod_3p.json`:

* `eventTypes` — объект по типу сущности: CAMPAIGN, AD_GROUP, AD, KEYWORD,
  NEGATIVE_KEYWORD, PRODUCT_TARGETING (THEME требует Accept v1.1);
* у кампаний объекты задаются через `eventTypeIds` (это её campaignId);
* у дочерних сущностей (ключи, таргеты, группы, объявления, негативы) — через
  `parents` (максимум 10 кампаний), поэтому история по ключам и таргетам
  вытягивается вместе с кампаниями, а не по одному объекту;
* `count` 50–200, страницы — `nextToken` (pageOffset упирается в 10000);
* окно — максимум 90 дней от часов сервиса.

Эндпоинт жёстко троттлится: 429 при частых вызовах, поэтому между запросами
пауза от 180 секунд, и запускать надо в фоне (nohup), а не в ssh-сессии.

    python -m ingest.history --days 88 --out data/inbox

Прогресс по батчам пишется в `<out>/history_progress.json`: обрыв связи или
таймаут не заставляют перечитывать уже собранные батчи.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://advertising-api.amazon.com"
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
DEFAULT_KEYFILE = Path.home() / "hermes-workspace/ads-amazon-key/secrets.env"

ENTITY_TYPES = ("CAMPAIGN", "AD_GROUP", "AD", "KEYWORD", "NEGATIVE_KEYWORD", "PRODUCT_TARGETING")
MAX_PARENTS = 10
ACCEPT = "application/vnd.historyresponse.v1.0+json"

# Что именно забираем по каждому типу. Ограничения официальные (список фильтров
# enum в схеме): IN_BUDGET у кампании — это постоянное «вошла/вышла из бюджета»,
# такие записи забивают страницы (200 строк на страницу, пауза 180 с) и не нужны
# в журнале ставок, статусов и бюджетов. Перечислять фильтры обязательно: без
# них сервис отдаёт и шум, и наши собственные пакетные правки ставок одинаково.
HISTORY_FILTERS = {
    "CAMPAIGN": ("BUDGET_AMOUNT", "STATUS", "NAME", "PLACEMENT_GROUP",
                 "SMART_BIDDING_STRATEGY", "START_DATE", "END_DATE"),
    "AD_GROUP": ("STATUS", "NAME", "BID_AMOUNT"),
    "AD": ("STATUS",),
    "KEYWORD": ("STATUS", "BID_AMOUNT"),
    "NEGATIVE_KEYWORD": ("STATUS",),
    "PRODUCT_TARGETING": ("STATUS", "BID_AMOUNT"),
}


class HistoryError(RuntimeError):
    """Выгрузка неполна или недоступна — данные писать нельзя."""


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def event_types_payload(campaign_ids: list[str], entity_types=ENTITY_TYPES,
                        filters: bool = True) -> dict:
    """Собрать блок `eventTypes` для батча кампаний (не больше 10 за запрос)."""
    ids = [str(c) for c in campaign_ids]
    if not ids:
        raise HistoryError("пустой батч кампаний")
    if len(ids) > MAX_PARENTS:
        raise HistoryError(f"в батче {len(ids)} кампаний — сервис принимает не больше {MAX_PARENTS}")
    payload: dict[str, dict] = {}
    for kind in entity_types:
        if kind not in ENTITY_TYPES:
            raise HistoryError(f"неизвестный тип сущности: {kind}")
        # Кампанию ищем по её ID, детей — по родителю: так один запрос отдаёт
        # всю историю ключей и таргетов кампаний батча.
        payload[kind] = ({"eventTypeIds": ids} if kind == "CAMPAIGN"
                         else {"parents": [{"campaignId": i, "useProfileIdAdvertiser": True} for i in ids]})
        if filters:
            payload[kind]["filters"] = list(HISTORY_FILTERS[kind])
    return payload


def history_request(campaign_ids: list[str], start_ms: int, end_ms: int, *,
                    entity_types=ENTITY_TYPES, count: int = 200,
                    next_token: str | None = None, filters: bool = True) -> dict:
    body = {
        "fromDate": int(start_ms),
        "toDate": int(end_ms),
        "eventTypes": event_types_payload(campaign_ids, entity_types, filters),
        "count": max(50, min(200, int(count))),
        # Направление задаём явно: без него сервис отвечает 400 plain-text.
        "sort": {"key": "DATE", "direction": "DESC"},
    }
    if next_token:
        body["nextToken"] = next_token
    return body


class Requester:
    """Токен + HTTP: обновление доступа при 401, терпеливая эскалация 429."""

    def __init__(self, env: dict[str, str], profile: str, *, pause: int = 180,
                 transport=None, sleep=time.sleep, log=print):
        self.env = env
        self.profile = str(profile)
        self.pause = pause
        self.sleep = sleep
        self.log = log
        self._transport = transport or self._http
        self._token = ""
        self._token_at = 0.0
        self.calls = 0

    @staticmethod
    def _http(method: str, url: str, headers: dict, body: bytes | None, timeout: int = 120):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode()
                try:
                    return response.status, json.loads(raw or "{}")
                except ValueError:
                    return response.status, {"raw": raw[:400]}
        except urllib.error.HTTPError as err:
            text = err.read().decode(errors="ignore")
            try:
                return err.code, json.loads(text or "{}")
            except ValueError:
                return err.code, {"raw": text[:400]}

    def token(self, force: bool = False) -> str:
        if self._token and not force and (time.time() - self._token_at) < 3000:  # < 50 мин
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.env["refresh_token"],
            "client_id": self.env["client_id"], "client_secret": self.env["client_secret"],
        }).encode()
        status, payload = self._transport("POST", TOKEN_URL,
                                          {"Content-Type": "application/x-www-form-urlencoded"},
                                          body, 60)
        if status != 200 or "access_token" not in payload:
            raise HistoryError(f"токен не получен: HTTP {status} {json.dumps(payload)[:200]}")
        self._token, self._token_at = payload["access_token"], time.time()
        return self._token

    def call(self, path: str, payload: dict, media: str = "application/json",
             tries: int = 6) -> tuple[int, dict]:
        """POST к API. 401 — один раз обновляем токен; 429 — ждём и повторяем."""
        refresh_used = False
        for attempt in range(tries):
            headers = {
                "Authorization": "Bearer " + self.token(force=refresh_used),
                "Amazon-Advertising-API-ClientId": self.env["client_id"],
                "Amazon-Advertising-API-Scope": self.profile,
                "Accept": media,
                "Content-Type": media,
            }
            self.calls += 1
            status, body = self._transport("POST", BASE + path, headers,
                                           json.dumps(payload).encode(), 120)
            if status == 401 and not refresh_used:
                refresh_used = True
                self.log("401 · обновляю токен", flush=True)
                continue
            if status == 429:
                wait = self.pause * (attempt + 1)
                self.log(f"429 · пауза {wait} с", flush=True)
                self.sleep(wait)
                continue
            if status >= 500:
                self.sleep(min(120, 10 * (attempt + 1)))
                continue
            return status, body
        raise HistoryError(f"{path}: сервис отвечает 429/5xx после {tries} попыток")


def list_campaigns(request: Requester) -> list[str]:
    status, body = request.call("/sp/campaigns/list",
                                {"stateFilter": {"include": ["ENABLED", "PAUSED", "ARCHIVED"]},
                                 "maxResults": 1000},
                                "application/vnd.spCampaign.v3+json")
    if status != 200:
        raise HistoryError(f"campaigns: HTTP {status} {json.dumps(body)[:200]}")
    return [str(c["campaignId"]) for c in body.get("campaigns", [])]


def batches(ids: list[str], size: int = MAX_PARENTS) -> list[list[str]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def pull_batch(request: Requester, ids: list[str], start_ms: int, end_ms: int, *,
               entity_types=ENTITY_TYPES, count: int = 200, pause: int = 180,
               filters: bool = True, log=print) -> list[dict]:
    """Собрать события одного батча кампаний, страница за страницей."""
    events: list[dict] = []
    token: str | None = None
    pages = 0
    while True:
        payload = history_request(ids, start_ms, end_ms, entity_types=entity_types,
                                  count=count, next_token=token, filters=filters)
        status, body = request.call("/history", payload, ACCEPT)
        if status != 200:
            raise HistoryError(f"history HTTP {status}: {json.dumps(body)[:300]}")
        page = [e for e in (body.get("events") or []) if isinstance(e, dict)]
        events.extend(page)
        pages += 1
        total = body.get("totalRecords")
        offset, page_size = body.get("pageOffset"), body.get("pageSize")
        max_page = body.get("maxPageNumber")
        log(f"  стр.{pages}: +{len(page)} (всего {len(events)}/{total})", flush=True)
        token = body.get("nextToken")
        if not token:
            # Полнота: страницы кончились И собранное совпало с totalRecords.
            if total in (None, len(events)):
                break
            raise HistoryError(
                f"неполная выгрузка батча: собрано {len(events)}, сервис заявил {total} "
                f"(pageSize={page_size} pageOffset={offset} maxPageNumber={max_page})")
        if page and page_size and max_page and (offset or 0) // page_size >= max_page:
            break
        time.sleep(pause)
    return events


def pull(ids: list[str], start_ms: int, end_ms: int, out: Path, request: Requester, *,
         entity_types=ENTITY_TYPES, count: int = 200, pause: int = 180,
         filters: bool = True, profile: str = "", log=print) -> dict:
    """Обойти все кампании батчами, сохраняя прогресс после каждого батча."""
    out.mkdir(parents=True, exist_ok=True)
    progress_path = out / "history_progress.json"
    events_path = out / "history_events.jsonl"
    progress = {"profile": str(profile), "to": end_ms, "batches": {}, "complete": False}
    if progress_path.is_file():
        saved = json.loads(progress_path.read_text(encoding="utf-8"))
        if saved.get("to") == end_ms and saved.get("profile") == str(profile):
            progress = saved
            log(f"возобновляю: готово батчей {len(progress['batches'])}", flush=True)
        else:
            log("окно или профиль изменились — прогресс начинается заново", flush=True)
            events_path.unlink(missing_ok=True)

    chunks = batches(ids)
    for index, chunk in enumerate(chunks):
        marker = ",".join(chunk)
        if marker in progress["batches"]:
            continue
        log(f"батч {index + 1}/{len(chunks)}: {len(chunk)} кампаний", flush=True)
        events = pull_batch(request, chunk, start_ms, end_ms, entity_types=entity_types,
                            count=count, pause=pause, filters=filters, log=log)
        with events_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        progress["batches"][marker] = len(events)
        progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
        if index + 1 < len(chunks):
            time.sleep(pause)

    collected: list[dict] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                collected.append(json.loads(line))
    progress["complete"] = len(progress["batches"]) == len(chunks)
    progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    return {"events": collected, "progress": progress, "chunks": len(chunks)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка истории изменений Amazon Ads")
    parser.add_argument("--days", type=int, default=88, help="окно в днях (сервис даёт максимум 90)")
    parser.add_argument("--out", default="data/inbox")
    parser.add_argument("--keys", default=str(DEFAULT_KEYFILE))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--pause", type=int, default=180)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--types", default=",".join(ENTITY_TYPES))
    parser.add_argument("--no-filters", action="store_true",
                        help="не ограничивать типы изменений (в журнал попадёт и «в бюджете»)")
    parser.add_argument("--campaigns", default="", help="ограничить список ID через запятую")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env = read_env(Path(args.keys))
    profile = str(args.profile or env.get("profile_id_us") or "")
    if not profile:
        raise SystemExit("в secrets.env нет profile_id_us — укажите --profile")

    request = Requester(env, profile, pause=args.pause)
    ids = [c.strip() for c in args.campaigns.split(",") if c.strip()] or list_campaigns(request)
    if not ids:
        raise HistoryError("в кабинете нет кампаний — выгрузка не нужна")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=min(max(args.days, 1), 88))
    types = tuple(t.strip() for t in args.types.split(",") if t.strip())
    print(f"профиль {profile} · кампаний {len(ids)} · типы {','.join(types)} · "
          f"{start.date()}..{now.date()}", flush=True)

    result = pull(ids, int(start.timestamp() * 1000), int(now.timestamp() * 1000), out, request,
                  entity_types=types, count=args.count, pause=args.pause,
                  filters=not args.no_filters, profile=profile)
    events = result["events"]
    target = out / f"history_{date.today().isoformat()}.json"
    target.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    by_type: dict[str, int] = {}
    for event in events:
        by_type[event.get("entityType", "?")] = by_type.get(event.get("entityType", "?"), 0) + 1
    manifest = {
        "profile": profile, "campaigns": len(ids), "types": list(types),
        "window": [start.isoformat(), now.isoformat()],
        "events": len(events), "by_type": by_type,
        "filters": not args.no_filters,
        "batches": len(result["progress"]["batches"]), "chunks": result["chunks"],
        "complete": result["progress"]["complete"], "api_calls": request.calls,
        "file": target.name,
    }
    (out / "history_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    print("DONE", json.dumps(manifest, ensure_ascii=False), flush=True)
    if not manifest["complete"]:
        raise SystemExit("выгрузка не завершена — прогресс сохранён, повторите запуск")


if __name__ == "__main__":
    main()
