"""Загрузка распознанных отчётов и снимков настроек в SQLite."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = Path(__file__).with_name("schema.sql")

METRICS = ("impressions", "clicks", "spend", "orders", "sales", "units")
METRIC_FIELDS = {
    "impressions": "metric.impressions",
    "clicks": "metric.clicks",
    "spend": "metric.totalCost",
    "orders": "metric.purchases",
    "sales": "metric.sales",
    "units": "metric.unitsSold",
}

FACT_TABLES = {
    "campaign_day": "fact_campaign_day",
    "adgroup_day": "fact_adgroup_day",
    "target_day": "fact_target_day",
    "searchterm_day": "fact_searchterm_day",
    "placement_day": "fact_placement_day",
    "product_day": "fact_product_day",
}

FACT_KEYS = {
    "campaign_day": ("date", "campaign_id"),
    "adgroup_day": ("date", "campaign_id", "ad_group_id"),
    "target_day": ("date", "campaign_id", "ad_group_id", "target_id"),
    "searchterm_day": ("date", "campaign_id", "ad_group_id", "target_id", "search_term"),
    "placement_day": ("date", "campaign_id", "placement"),
    "product_day": ("date", "campaign_id", "ad_group_id", "ad_id", "asin", "marketplace"),
}

FACT_COLUMNS = {
    "campaign_day": ("campaign_name", "currency"),
    "adgroup_day": ("currency",),
    "target_day": ("targeting", "match_type", "currency"),
    "searchterm_day": ("targeting", "match_type", "currency"),
    "placement_day": ("placement_raw", "currency"),
    "product_day": ("currency",),
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI выполняет sync-эндпоинты в пуле потоков: соединение создаётся и
    # закрывается в разных потоках, поэтому check_same_thread выключаем.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", ".")) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    return int(round(_num(value)))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _disp(value: Any) -> str:
    """Показать значение без хвоста .0 у чисел."""
    if value is None:
        return ""
    try:
        number = float(value)
        return f"{number:g}"
    except (TypeError, ValueError):
        return str(value)


def normalize_match(value: str) -> str:
    return "AUTO" if value == "TARGETING_EXPRESSION_PREDEFINED" else (value or "")


def _report_row(raw: dict[str, Any]) -> dict[str, Any]:
    match = normalize_match(_text(raw.get("targetingMatchType.value")))
    placement_raw = _text(raw.get("placementClassification.value"))
    return {
        "date": _text(raw.get("date.value"))[:10],
        "campaign_id": _text(raw.get("campaign.id")),
        "campaign_name": _text(raw.get("campaign.name")),
        "ad_group_id": _text(raw.get("adGroup.id")),
        "target_id": _text(raw.get("adGroupTarget.id")),
        "ad_id": _text(raw.get("ad.id")),
        "asin": _text(raw.get("advertisedProduct.id")),
        "marketplace": _text(raw.get("advertisedProductMarketplace.value")),
        "targeting": _text(raw.get("targeting.value")),
        "match_type": match,
        "search_term": _text(raw.get("searchTerm.value")),
        "placement": placement_raw or "__UNKNOWN__",
        "placement_raw": placement_raw,
        "currency": _text(raw.get("budgetCurrency.value")) or "USD",
        **{name: _num(raw.get(col)) for name, col in METRIC_FIELDS.items()},
    }


def load_activity(
    conn: sqlite3.Connection,
    kind: str,
    rows: Iterable[dict[str, Any]],
    source: str,
    loaded_at: str,
) -> int:
    """Загрузить строки отчёта, сложив дубликаты по зерну."""
    table = FACT_TABLES[kind]
    keys = FACT_KEYS[kind]
    grouped: dict[tuple, dict[str, Any]] = {}
    for raw in rows:
        row = _report_row(raw)
        if not row["date"] or not row["campaign_id"]:
            continue
        key = tuple(row[k] for k in keys)
        slot = grouped.get(key)
        if slot is None:
            slot = dict(row)
            slot["source"] = source
            slot["loaded_at"] = loaded_at
            grouped[key] = slot
        else:
            for metric in METRICS:
                slot[metric] += row[metric]
    if not grouped:
        return 0

    columns = list(keys) + list(FACT_COLUMNS[kind]) + list(METRICS) + ["source", "loaded_at"]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [tuple(item[c] for c in columns) for item in grouped.values()])
    conn.commit()
    return len(grouped)


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _add_change(
    conn: sqlite3.Connection,
    *,
    change_id: str,
    change_date: str,
    change_ts: str,
    precision: str,
    entity_type: str,
    entity_id: str,
    campaign_id: str,
    ad_group_id: str,
    label: str,
    field: str,
    old: str,
    new: str,
    source: str,
    actor: str,
    summary: str | None = None,
) -> None:
    old_d, new_d = _disp(old), _disp(new)
    if summary is None:
        summary = f"{label or entity_id} · {field}: {old_d or '—'} → {new_d or '—'}"
    conn.execute(
        """INSERT OR IGNORE INTO changes
           (change_id, change_date, change_ts, date_precision, entity_type, entity_id,
            campaign_id, ad_group_id, entity_label, field, old_value, new_value,
            delta, source, actor, summary, first_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            change_id, change_date, change_ts, precision, entity_type, entity_id,
            campaign_id, ad_group_id, label, field, old_d, new_d,
            _delta(old, new), source, actor, summary,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def _delta(old: str, new: str) -> str:
    try:
        return f"{round(float(new) - float(old), 6):g}"
    except (TypeError, ValueError):
        return ""


TRACKED = {
    "campaigns": {
        "table": "dim_campaign",
        "pk": "campaign_id",
        "entity": "CAMPAIGN",
        "fields": ("name", "state", "budget", "budget_type", "bid_strategy", "adj_top",
                   "adj_product_page", "adj_rest", "targeting_type", "start_date", "end_date"),
        "labels": {"budget": "BUDGET_AMOUNT", "state": "STATUS", "bid_strategy": "BIDDING_STRATEGY",
                   "adj_top": "PLACEMENT_TOP", "adj_product_page": "PLACEMENT_PRODUCT_PAGE",
                   "adj_rest": "PLACEMENT_REST_OF_SEARCH", "name": "NAME"},
    },
    "adgroups": {
        "table": "dim_adgroup",
        "pk": "ad_group_id",
        "entity": "AD_GROUP",
        "fields": ("name", "state", "default_bid"),
        "labels": {"default_bid": "DEFAULT_BID", "state": "STATUS", "name": "NAME"},
    },
    "targets": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "TARGET",
        "fields": ("state", "bid", "match_type", "targeting"),
        "labels": {"bid": "BID", "state": "STATUS", "targeting": "TARGETING"},
    },
    "negative_targets": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "NEGATIVE_TARGET",
        "fields": ("state", "targeting", "match_type"),
        "labels": {"state": "STATUS", "targeting": "TARGETING"},
    },
    "campaign_negative_targets": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "CAMPAIGN_NEGATIVE_TARGET",
        "fields": ("state", "targeting", "match_type"),
        "labels": {"state": "STATUS", "targeting": "TARGETING"},
    },
    "keywords": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "KEYWORD",
        "fields": ("state", "bid", "match_type", "targeting"),
        "labels": {"bid": "BID", "state": "STATUS", "targeting": "KEYWORD"},
    },
    "negative_keywords": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "NEGATIVE_KEYWORD",
        "fields": ("state", "match_type", "targeting"),
        "labels": {"state": "STATUS", "targeting": "KEYWORD"},
    },
    "campaign_negative_keywords": {
        "table": "dim_target",
        "pk": "target_id",
        "entity": "CAMPAIGN_NEGATIVE_KEYWORD",
        "fields": ("state", "match_type", "targeting"),
        "labels": {"state": "STATUS", "targeting": "KEYWORD"},
    },
    "ads": {
        "table": "dim_ad",
        "pk": "ad_id",
        "entity": "AD",
        "fields": ("state", "asin"),
        "labels": {"state": "STATUS", "asin": "ASIN"},
    },
}


def _expression(row: dict[str, Any]) -> str:
    items = []
    for item in row.get("expression") or []:
        kind, value = item.get("type"), item.get("value")
        if isinstance(value, (dict, list)):
            items.append(f"{kind}:{json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        elif value is None:
            items.append(str(kind))
        else:
            items.append(f"{kind}:{value}")
    return ";".join(items)


def _auto_match(row: dict[str, Any]) -> str:
    expr = row.get("expression") or []
    if len(expr) != 1:
        return ""
    mapping = {
        "QUERY_HIGH_REL_MATCHES": "AUTO_CLOSE",
        "QUERY_BROAD_REL_MATCHES": "AUTO_LOOSE",
        "ASIN_SUBSTITUTE_RELATED": "AUTO_SUBSTITUTES",
        "ASIN_ACCESSORY_RELATED": "AUTO_COMPLEMENTS",
    }
    return mapping.get(expr[0].get("type"), "")


def _serving(row: dict[str, Any]) -> str:
    return _text((row.get("extendedData") or {}).get("servingStatus"))


def _timestamps(row: dict[str, Any]) -> tuple[str, str]:
    ext = row.get("extendedData") or {}
    return _text(ext.get("creationDateTime")), _text(ext.get("lastUpdateDateTime"))


def load_settings(
    conn: sqlite3.Connection,
    kind: str,
    rows: Iterable[dict[str, Any]],
    snapshot_at: str,
    emit_creates: bool = True,
) -> dict[str, int]:
    """Обновить справочник и записать изменения в журнал.

    Возвращает счётчики: new / updated / changes.
    """
    spec = TRACKED[kind]
    table, pk, fields = spec["table"], spec["pk"], spec["fields"]
    now = snapshot_at
    new = updated = 0

    group_bids: dict[str, float] = {}
    if kind == "adgroups":
        group_bids = {_text(r.get("adGroupId")): _num(r.get("defaultBid")) for r in rows}

    materialized: list[dict[str, Any]] = []
    for row in rows:
        created, modified = _timestamps(row)
        base = {
            "campaign_id": _text(row.get("campaignId")),
            "ad_group_id": _text(row.get("adGroupId")),
            "created_at": created,
            "updated_at": modified,
            "serving_status": _serving(row),
            "last_seen": now,
        }
        if kind == "campaigns":
            budget = row.get("budget") or {}
            bidding = row.get("dynamicBidding") or {}
            adjustments = {
                p.get("placement"): _num(p.get("percentage"))
                for p in bidding.get("placementBidding") or []
            }
            item = {
                "campaign_id": _text(row.get("campaignId")),
                "name": _text(row.get("name")),
                "state": _text(row.get("state")),
                "targeting_type": _text(row.get("targetingType")),
                "budget": _num(budget.get("budget")),
                "budget_type": _text(budget.get("budgetType")),
                "bid_strategy": _text(bidding.get("strategy")),
                "adj_top": adjustments.get("PLACEMENT_TOP", 0.0),
                "adj_product_page": adjustments.get("PLACEMENT_PRODUCT_PAGE", 0.0),
                "adj_rest": adjustments.get("PLACEMENT_REST_OF_SEARCH", 0.0),
                "start_date": _text(row.get("startDate")),
                "end_date": _text(row.get("endDate")),
                "created_at": created,
                "updated_at": modified,
                "serving_status": _serving(row),
                "last_seen": now,
            }
        elif kind == "adgroups":
            item = {
                "ad_group_id": _text(row.get("adGroupId")),
                "campaign_id": _text(row.get("campaignId")),
                "name": _text(row.get("name")),
                "state": _text(row.get("state")),
                "default_bid": _num(row.get("defaultBid")),
                "created_at": created,
                "updated_at": modified,
                "serving_status": _serving(row),
                "last_seen": now,
            }
        elif kind == "ads":
            item = {
                "ad_id": _text(row.get("adId")),
                "campaign_id": _text(row.get("campaignId")),
                "ad_group_id": _text(row.get("adGroupId")),
                "asin": _text(row.get("asin")),
                "sku": _text(row.get("sku")),
                "state": _text(row.get("state")),
                "created_at": created,
                "updated_at": modified,
                "serving_status": _serving(row),
                "last_seen": now,
            }
        else:
            negative = "negative" in kind
            level = "CAMPAIGN" if kind.startswith("campaign_") else ("AD_GROUP" if negative else None)
            if "keywordId" in row:
                tid = _text(row.get("keywordId"))
                targeting = _text(row.get("keywordText"))
                match_type = _text(row.get("matchType"))
                entity_kind = "KEYWORD"
            else:
                tid = _text(row.get("targetId"))
                targeting = _expression(row)
                match_type = _auto_match(row)
                entity_kind = "AUTO" if row.get("expressionType") == "AUTO" else "PRODUCT"
            gid = _text(row.get("adGroupId"))
            own = None if negative else row.get("bid")
            if own is not None and _text(own) != "":
                bid, bid_source, effective = _num(own), "OWN", _num(own)
            elif not negative and gid in group_bids:
                bid, bid_source, effective = None, "AD_GROUP_DEFAULT", group_bids[gid]
            else:
                bid, bid_source, effective = None, ("NONE" if negative else "UNKNOWN"), None
            item = {
                "target_id": tid,
                "campaign_id": _text(row.get("campaignId")),
                "ad_group_id": gid,
                "kind": entity_kind,
                "targeting": targeting,
                "match_type": match_type,
                "is_negative": 1 if negative else 0,
                "negative_level": level,
                "state": _text(row.get("state")),
                "bid": bid,
                "bid_source": bid_source,
                "effective_bid": effective,
                "created_at": created,
                "updated_at": modified,
                "serving_status": _serving(row),
                "last_seen": now,
            }
        materialized.append(item)

    for item in materialized:
        entity_id = _text(item[pk])
        if not entity_id:
            continue
        existing = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (entity_id,)).fetchone()
        if existing is None:
            new += 1
            label = item.get("name") or item.get("targeting") or entity_id
            # Первичная загрузка не засоряет журнал тысячей событий «создан».
            if emit_creates:
                _add_change(
                    conn,
                    change_id=_sha("create", kind, entity_id),
                    change_date=now[:10],
                    change_ts=item.get("created_at") or now,
                    precision="ENTITY_TS" if item.get("created_at") else "SNAPSHOT",
                    entity_type=spec["entity"],
                    entity_id=entity_id,
                    campaign_id=_text(item.get("campaign_id")),
                    ad_group_id=_text(item.get("ad_group_id")),
                    label=label,
                    field="CREATED",
                    old="",
                    new="CREATED",
                    source="SNAPSHOT_DIFF",
                    actor="UNKNOWN",
                )
        else:
            diffs = []
            for field in fields:
                old, new_value = existing[field], item.get(field)
                if field in ("budget", "bid", "default_bid", "adj_top", "adj_product_page",
                             "adj_rest", "effective_bid"):
                    if old is None and new_value is None:
                        continue
                    if abs(_num(old) - _num(new_value)) < 1e-9:
                        continue
                elif _text(old) == _text(new_value):
                    continue
                diffs.append((field, _text(old), _text(new_value)))
            if diffs:
                updated += 1
                label = item.get("name") or item.get("targeting") or entity_id
                ts = item.get("updated_at") or now
                for field, old, new_value in diffs:
                    label_field = spec["labels"].get(field, field.upper())
                    _add_change(
                        conn,
                        change_id=_sha(kind, entity_id, field, old, new_value, ts),
                        change_date=(ts or now)[:10],
                        change_ts=ts,
                        precision="ENTITY_TS" if item.get("updated_at") else "SNAPSHOT",
                        entity_type=spec["entity"],
                        entity_id=entity_id,
                        campaign_id=_text(item.get("campaign_id")),
                        ad_group_id=_text(item.get("ad_group_id")),
                        label=label,
                        field=label_field,
                        old=old,
                        new=new_value,
                        source="SNAPSHOT_DIFF",
                        actor="UNKNOWN",
                    )
        columns = [c for c in item if c in _columns(conn, table)]
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != pk)
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({pk}) DO UPDATE SET {updates}",
            tuple(item[c] for c in columns),
        )

    conn.commit()
    changes = conn.execute("SELECT COUNT(*) FROM changes WHERE first_seen=?", (now,)).fetchone()[0]
    return {"new": new, "updated": updated, "changes": changes}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def import_changes(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Импорт готового журнала: наш x_changes или события POST /history Amazon."""
    count = 0
    for row in rows:
        if "change_id" in row and "field" in row:
            change_date = _text(row.get("change_date"))[:10]
            conn.execute(
                """INSERT OR IGNORE INTO changes
                   (change_id, change_date, change_ts, date_precision, entity_type, entity_id,
                    campaign_id, ad_group_id, entity_label, field, old_value, new_value,
                    delta, source, actor, summary, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _text(row.get("change_id")), change_date, _text(row.get("change_ts")),
                    _text(row.get("date_precision")) or "DAY", _text(row.get("entity_type")),
                    _text(row.get("entity_id")), _text(row.get("campaign_id")),
                    _text(row.get("ad_group_id")), _text(row.get("entity_label")),
                    _text(row.get("field")), _text(row.get("old_value")), _text(row.get("new_value")),
                    _text(row.get("delta")), _text(row.get("source")) or "IMPORT",
                    _text(row.get("actor")) or "UNKNOWN", _text(row.get("summary")),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            count += 1
        elif "changeType" in row:
            ts_ms = row.get("timestamp")
            ts = datetime.fromtimestamp(float(ts_ms) / 1000, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
            meta = row.get("metadata") or {}
            campaign_id = _text(meta.get("campaignId"))
            ad_group_id = _text(meta.get("adGroupId"))
            field = _text(row.get("changeType"))
            if field == "PLACEMENT_GROUP" and meta.get("placementGroupPosition"):
                field = f"PLACEMENT_{meta['placementGroupPosition']}"
            old, new = _text(row.get("previousValue")), _text(row.get("newValue"))
            entity_id = _text(row.get("entityId"))
            _add_change(
                conn,
                change_id=_sha("history", entity_id, field, old, new, ts.isoformat()),
                change_date=ts.date().isoformat(),
                change_ts=ts.isoformat(),
                precision="EXACT_TS",
                entity_type=_text(row.get("entityType")),
                entity_id=entity_id,
                campaign_id=campaign_id,
                ad_group_id=ad_group_id,
                label=campaign_id,
                field=field,
                old=old,
                new=new,
                source="AMAZON_HISTORY",
                actor="UNKNOWN",
            )
            count += 1
    conn.commit()
    return count


def refresh_coverage(conn: sqlite3.Connection, run_id: str) -> None:
    for dataset, table in FACT_TABLES.items():
        if "spend" not in _columns(conn, table):
            continue
        conn.execute("DELETE FROM coverage WHERE dataset=?", (dataset,))
        conn.execute(
            f"""INSERT INTO coverage (dataset, date, rows, spend, status, run_id)
                SELECT ?, date, COUNT(*), ROUND(SUM(spend), 2), 'OK', ?
                FROM {table} GROUP BY date""",
            (dataset, run_id),
        )
    conn.commit()


def start_run(conn: sqlite3.Connection, run_id: str, source: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_runs (run_id, started_at, status, source) VALUES (?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), "RUNNING", source),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, files: int, rows: int, status: str = "OK", note: str = "") -> None:
    conn.execute(
        """UPDATE sync_runs SET finished_at=?, status=?, files=?, rows=?, note=? WHERE run_id=?""",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), status, files, rows, note, run_id),
    )
    conn.commit()


def import_folder(conn: sqlite3.Connection, folder: Path, run_id: str) -> dict[str, Any]:
    """Главный вход: папка с любыми отчётами Amazon Ads."""
    from .detect import scan_folder

    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    first_import = conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] <= 1
    found = scan_folder(folder)
    stats: dict[str, Any] = defaultdict(int)
    details = []
    for item in found:
        kind = item["kind"]
        if not kind:
            stats["skipped"] += 1
            details.append({"file": item["path"].name, "kind": None, "rows": len(item["rows"]), "status": "unknown"})
            continue
        if item["category"] == "activity":
            loaded_by_kind = {}
            for dataset in kind:
                loaded_by_kind[dataset] = load_activity(
                    conn, dataset, item["rows"], f"file:{item['path'].name}", snapshot_at
                )
                stats["facts"] += loaded_by_kind[dataset]
                stats[f"fact_{dataset}"] += loaded_by_kind[dataset]
            details.append({"file": item["path"].name, "kind": kind, "rows": len(item["rows"]),
                            "loaded": loaded_by_kind})
        elif item["category"] == "settings" or item["category"] == "negative":
            result = load_settings(conn, kind, item["rows"], snapshot_at,
                                   emit_creates=not first_import)
            stats["settings"] += result["new"] + result["updated"]
            details.append({"file": item["path"].name, "kind": kind, "rows": len(item["rows"]), **result})
        else:
            imported = import_changes(conn, item["rows"])
            stats["changes"] += imported
            details.append({"file": item["path"].name, "kind": "changes", "rows": len(item["rows"]), "loaded": imported})
    refresh_coverage(conn, run_id)
    total_rows = sum(int(v) for k, v in stats.items() if k in ("facts", "settings", "changes"))
    finish_run(conn, run_id, len(found), total_rows, "OK", json.dumps(details, ensure_ascii=False))
    return {"files": len(found), "stats": dict(stats), "details": details}
