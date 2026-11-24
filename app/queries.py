"""Аналитические запросы к базе дашборда."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

METRICS = ("impressions", "clicks", "spend", "orders", "sales", "units")

# Как эти размещения называются в кабинете Amazon Ads.
PLACEMENT_LABELS = {
    "TOP OF SEARCH ON-AMAZON": "Top of Search",
    "TOP OF SEARCH OFF-AMAZON": "Top of Search",
    "REST OF SEARCH ON-AMAZON": "Rest of Search",
    "REST OF SEARCH OFF-AMAZON": "Rest of Search",
    "DETAIL PAGE ON-AMAZON": "Product page",
    "DETAIL PAGE OFF-AMAZON": "Product page",
    "PRODUCT_PAGES": "Product page",
    "PRODUCT PAGE": "Product page",
    "OTHER ON-AMAZON": "Rest of Search",
    "OTHER OFF-AMAZON": "Rest of Search",
    "TOP_OF_SEARCH": "Top of Search",
    "REST_OF_SEARCH": "Rest of Search",
}


def placement_label(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value or value == "__UNKNOWN__":
        return "Не определено"
    return PLACEMENT_LABELS.get(value.upper(), value.replace(" ON-AMAZON", "").replace(" OFF-AMAZON", "").title())


# Авто-таргеты Amazon приходят кодами (QUERY_HIGH_REL_MATCHES, ASIN_SAME_AS:B0…).
# Владельцу нужен смысл, а не код; исходное значение остаётся в подсказке.
AUTO_TARGET_LABELS = {
    "QUERY_HIGH_REL_MATCHES": "авто · близкие запросы",
    "QUERY_BROAD_REL_MATCHES": "авто · широкие запросы",
    "ASIN_SUBSTITUTE_RELATED": "авто · замена",
    "ASIN_ACCESSORY_RELATED": "авто · дополнение",
    "ASIN_SAME_AS": "авто · тот же товар",
    "CLOSE-MATCH": "авто · близкие запросы",
    "LOOSE-MATCH": "авто · широкие запросы",
    "SUBSTITUTES": "авто · замена",
    "COMPLEMENTS": "авто · дополнение",
}


def targeting_label(value: str | None) -> str:
    """Читаемое название таргета: код авто-таргета переводим, ключ оставляем как есть."""
    text = (value or "").strip()
    if not text:
        return ""
    head, _, tail = text.partition(":")
    label = AUTO_TARGET_LABELS.get(head.strip().upper())
    if not label:
        return text
    return f"{label} · {tail}" if tail else label


def _camp_filter(campaigns: list[str] | None, column: str = "campaign_id") -> tuple[str, list[str]]:
    if not campaigns:
        return "", []
    placeholders = ",".join("?" for _ in campaigns)
    return f" AND {column} IN ({placeholders})", list(campaigns)


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def with_ratios(row: dict[str, Any]) -> dict[str, Any]:
    clicks = row.get("clicks") or 0
    impressions = row.get("impressions") or 0
    spend = row.get("spend") or 0
    sales = row.get("sales") or 0
    orders = row.get("orders") or 0
    row["ctr"] = round(clicks / impressions * 100, 3) if impressions else None
    row["cpc"] = round(spend / clicks, 4) if clicks else None
    row["cpa"] = round(spend / orders, 2) if orders else None
    row["acos"] = round(spend / sales * 100, 2) if sales else None
    row["roas"] = round(sales / spend, 3) if spend else None
    row["cvr"] = round(orders / clicks * 100, 2) if clicks else None
    row["aov"] = round(sales / orders, 2) if orders else None
    return row


def _totals(conn: sqlite3.Connection, table: str, start: str, end: str,
            campaigns: list[str] | None) -> dict[str, float]:
    where, params = _camp_filter(campaigns)
    row = conn.execute(
        f"""SELECT COALESCE(SUM(impressions),0) impressions, COALESCE(SUM(clicks),0) clicks,
                   COALESCE(SUM(spend),0) spend, COALESCE(SUM(orders),0) orders,
                   COALESCE(SUM(sales),0) sales, COALESCE(SUM(units),0) units
            FROM {table} WHERE date BETWEEN ? AND ? {where}""",
        [start, end, *params],
    ).fetchone()
    return dict(row)


def overview(conn: sqlite3.Connection, start: str, end: str,
             campaigns: list[str] | None, grain: str = "day") -> dict[str, Any]:
    current = with_ratios(_totals(conn, "fact_campaign_day", start, end, campaigns))
    length = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    prev_end = date.fromisoformat(start) - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    previous = with_ratios(
        _totals(conn, "fact_campaign_day", prev_start.isoformat(), prev_end.isoformat(), campaigns)
    )

    where, params = _camp_filter(campaigns)
    if grain == "week":
        bucket = "date(d, 'weekday 0', '-6 days')"
    elif grain == "month":
        bucket = "substr(d, 1, 7) || '-01'"
    else:
        bucket = "d"
    series_rows = conn.execute(
        f"""SELECT {bucket} AS bucket, SUM(impressions) impressions, SUM(clicks) clicks,
                   SUM(spend) spend, SUM(orders) orders, SUM(sales) sales, SUM(units) units
            FROM (SELECT date AS d, impressions, clicks, spend, orders, sales, units
                  FROM fact_campaign_day WHERE date BETWEEN ? AND ? {where})
            GROUP BY bucket ORDER BY bucket""",
        [start, end, *params],
    ).fetchall()
    series = [with_ratios(dict(r)) for r in series_rows]

    top = conn.execute(
        f"""SELECT f.campaign_id,
                   COALESCE(c.name, MAX(f.campaign_name)) name,
                   c.state, c.budget, c.bid_strategy,
                   SUM(f.impressions) impressions, SUM(f.clicks) clicks, SUM(f.spend) spend,
                   SUM(f.orders) orders, SUM(f.sales) sales, SUM(f.units) units
            FROM fact_campaign_day f LEFT JOIN dim_campaign c ON c.campaign_id=f.campaign_id
            WHERE f.date BETWEEN ? AND ? {where.replace('campaign_id','f.campaign_id')}
            GROUP BY f.campaign_id ORDER BY spend DESC""",
        [start, end, *params],
    ).fetchall()
    top_rows = [with_ratios(dict(r)) for r in top]

    coverage = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM fact_campaign_day"
    ).fetchone()

    return {
        "period": {"start": start, "end": end, "days": length,
                   "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat()}},
        "current": current,
        "previous": previous,
        "series": series,
        "campaigns": top_rows,
        "data_range": {"min": coverage[0], "max": coverage[1], "days": coverage[2]},
    }


def campaigns(conn: sqlite3.Connection, start: str, end: str,
              campaigns_filter: list[str] | None) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter, "c.campaign_id")
    rows = conn.execute(
        f"""SELECT c.campaign_id, c.name, c.state, c.targeting_type, c.budget, c.budget_type,
                   c.bid_strategy, c.adj_top, c.adj_product_page, c.adj_rest,
                   c.serving_status, c.start_date, c.updated_at,
                   COALESCE(f.impressions,0) impressions, COALESCE(f.clicks,0) clicks,
                   COALESCE(f.spend,0) spend, COALESCE(f.orders,0) orders,
                   COALESCE(f.sales,0) sales, COALESCE(f.units,0) units
            FROM dim_campaign c
            LEFT JOIN (SELECT campaign_id, SUM(impressions) impressions, SUM(clicks) clicks,
                              SUM(spend) spend, SUM(orders) orders, SUM(sales) sales, SUM(units) units
                       FROM fact_campaign_day WHERE date BETWEEN ? AND ?
                       GROUP BY campaign_id) f ON f.campaign_id=c.campaign_id
            WHERE 1=1 {where}
            ORDER BY COALESCE(f.spend,0) DESC, c.name""",
        [start, end, *params],
    ).fetchall()
    return [with_ratios(dict(r)) for r in rows]


def targets(conn: sqlite3.Connection, start: str, end: str,
            campaigns_filter: list[str] | None, kind: str = "positive",
            search: str | None = None, only_idle: bool = False) -> list[dict[str, Any]]:
    """Объекты таргетинга за период: метрики, ставка, дата добавления, последнее событие.

    Лимита на количество строк нет: таблица показывает все объекты, а страницы
    рисует интерфейс — тихое усечение списка выдаёт «объект пропал» за «объекта нет».
    """
    where, params = _camp_filter(campaigns_filter, "t.campaign_id")
    if kind == "negative":
        where += " AND t.is_negative=1"
    elif kind == "positive":
        where += " AND t.is_negative=0"
    if search:
        where += " AND (t.targeting LIKE ? OR t.match_type LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    rows = conn.execute(
        f"""SELECT t.target_id, t.campaign_id, t.ad_group_id, t.kind, t.targeting, t.match_type,
                   t.is_negative, t.negative_level, t.state, t.bid, t.bid_source, t.effective_bid,
                   t.serving_status, t.created_at, t.updated_at,
                   COALESCE(c.name, '') campaign_name, COALESCE(g.name, '') ad_group_name,
                   g.default_bid group_default_bid,
                   COALESCE(f.impressions,0) impressions, COALESCE(f.clicks,0) clicks,
                   COALESCE(f.spend,0) spend, COALESCE(f.orders,0) orders,
                   COALESCE(f.sales,0) sales, COALESCE(f.units,0) units,
                   CASE WHEN COALESCE(f.impressions,0)=0 AND COALESCE(f.clicks,0)=0
                        THEN 1 ELSE 0 END idle,
                   ch.field last_field, ch.old_value last_old, ch.new_value last_new,
                   ch.change_date last_date, ch.change_ts last_ts, ch.source last_source,
                   ch.date_precision last_precision
            FROM dim_target t
            LEFT JOIN dim_campaign c ON c.campaign_id=t.campaign_id
            LEFT JOIN dim_adgroup g ON g.ad_group_id=t.ad_group_id
            LEFT JOIN (SELECT campaign_id, ad_group_id, target_id,
                              SUM(impressions) impressions, SUM(clicks) clicks, SUM(spend) spend,
                              SUM(orders) orders, SUM(sales) sales, SUM(units) units
                       FROM fact_target_day WHERE date BETWEEN ? AND ?
                       GROUP BY campaign_id, ad_group_id, target_id) f
                   ON f.campaign_id=t.campaign_id AND f.ad_group_id=t.ad_group_id AND f.target_id=t.target_id
            LEFT JOIN (SELECT entity_id, field, old_value, new_value, change_date, change_ts,
                              source, date_precision,
                              ROW_NUMBER() OVER (PARTITION BY entity_id
                                                 ORDER BY change_date DESC, change_ts DESC) rn
                       FROM changes WHERE entity_type IN {TARGET_TYPES}) ch
                   ON ch.entity_id=t.target_id AND ch.rn=1
            WHERE 1=1 {where}
            ORDER BY t.is_negative, COALESCE(f.spend,0) DESC, t.targeting
            """,
        [start, end, *params],
    ).fetchall()
    result = [with_ratios(dict(r)) for r in rows]
    for row in result:
        row["targeting_raw"] = row["targeting"]
        row["targeting"] = targeting_label(row["targeting"])
    # Ставка объекта без собственного значения наследуется от группы. Считаем это
    # при чтении тоже: в базе есть записи, загруженные до починки импорта, и они
    # иначе показывают «ставка не задана» при живой ставке группы.
    for row in result:
        if (not row["is_negative"] and row["bid"] is None and row["effective_bid"] is None
                and row["group_default_bid"] is not None):
            row["effective_bid"] = row["group_default_bid"]
            row["bid_source"] = "AD_GROUP_DEFAULT"
        row.pop("group_default_bid", None)
    if only_idle:
        result = [r for r in result if r["idle"]]
    return result


def search_terms(conn: sqlite3.Connection, start: str, end: str,
                 campaigns_filter: list[str] | None, search: str | None = None,
                 only_orders: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter)
    if search:
        where += " AND search_term LIKE ?"
        params.append(f"%{search}%")
    having = " HAVING SUM(orders) > 0" if only_orders else ""
    rows = conn.execute(
        f"""SELECT s.search_term, s.campaign_id, COALESCE(c.name, '') campaign_name,
                   s.ad_group_id, s.target_id, s.targeting, s.match_type,
                   SUM(s.impressions) impressions, SUM(s.clicks) clicks, SUM(s.spend) spend,
                   SUM(s.orders) orders, SUM(s.sales) sales, SUM(s.units) units
            FROM fact_searchterm_day s
            LEFT JOIN dim_campaign c ON c.campaign_id = s.campaign_id
            WHERE s.date BETWEEN ? AND ? {where.replace('campaign_id', 's.campaign_id')}
            GROUP BY s.search_term, s.campaign_id, c.name {having}
            ORDER BY spend DESC LIMIT {int(limit)}""",
        [start, end, *params],
    ).fetchall()
    return [with_ratios(dict(r)) for r in rows]


def placements(conn: sqlite3.Connection, start: str, end: str,
               campaigns_filter: list[str] | None) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter, "p.campaign_id")
    rows = conn.execute(
        f"""SELECT p.placement, p.campaign_id, COALESCE(c.name, '') campaign_name,
                   SUM(p.impressions) impressions, SUM(p.clicks) clicks, SUM(p.spend) spend,
                   SUM(p.orders) orders, SUM(p.sales) sales, SUM(p.units) units
            FROM fact_placement_day p LEFT JOIN dim_campaign c ON c.campaign_id=p.campaign_id
            WHERE p.date BETWEEN ? AND ? {where}
            GROUP BY p.placement, p.campaign_id ORDER BY spend DESC""",
        [start, end, *params],
    ).fetchall()

    # On-Amazon и Off-Amazon одного размещения — одна строка в кабинете.
    merged: dict[tuple[str, str], dict] = {}
    for raw in rows:
        row = dict(raw)
        raw_value = row.pop("placement") or "Не определено"
        label = placement_label(raw_value)
        key = (row["campaign_id"], label)
        slot = merged.setdefault(key, {**row, "placement": label, "placement_raws": set()})
        slot["placement_raws"].add(raw_value)
        for metric in METRICS:
            slot[metric] = (slot.get(metric) or 0) + (row.get(metric) or 0)
    output = []
    for slot in merged.values():
        slot["placement_raws"] = ", ".join(sorted(slot["placement_raws"]))
        output.append(with_ratios(slot))
    output.sort(key=lambda r: r["spend"], reverse=True)
    return output


def products(conn: sqlite3.Connection, start: str, end: str,
             campaigns_filter: list[str] | None) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter)
    rows = conn.execute(
        f"""SELECT f.asin, f.marketplace, f.campaign_id, COALESCE(c.name, '') campaign_name,
                   SUM(f.impressions) impressions, SUM(f.clicks) clicks,
                   SUM(f.spend) spend, SUM(f.orders) orders, SUM(f.sales) sales, SUM(f.units) units
            FROM fact_product_day f LEFT JOIN dim_campaign c ON c.campaign_id = f.campaign_id
            WHERE f.date BETWEEN ? AND ? {where.replace('campaign_id', 'f.campaign_id')}
            GROUP BY f.asin, f.marketplace, f.campaign_id, c.name
            ORDER BY spend DESC""",
        [start, end, *params],
    ).fetchall()
    return [with_ratios(dict(r)) for r in rows]


# Типы сущностей журнала, которые живут в dim_target: Amazon отдаёт ключи как
# KEYWORD, ручные и авто-таргеты — как PRODUCT_TARGETING.
TARGET_TYPES = ('KEYWORD', 'TARGET', 'PRODUCT_TARGETING', 'NEGATIVE_KEYWORD', 'NEGATIVE_TARGET',
                'CAMPAIGN_NEGATIVE_KEYWORD', 'CAMPAIGN_NEGATIVE_TARGET')

# Группы полей журнала для фильтра «что менялось»: заголовки в интерфейсе
# совпадают с ключами, поэтому сервер и статика читают одну таблицу.
FIELD_GROUPS = {
    'status': ("STATUS",),
    'bid': ("BID_AMOUNT", "DEFAULT_BID_AMOUNT", "BID"),
    'budget': ("BUDGET_AMOUNT", "IN_BUDGET"),
    'placement': ("PLACEMENT_TOP", "PLACEMENT_DETAIL_PAGE", "PLACEMENT_REST_OF_SEARCH",
                  "PLACEMENT_PRODUCT_PAGE"),
    'name': ("NAME",),
    'created': ("CREATED",),
}


def changes(conn: sqlite3.Connection, start: str | None = None, end: str | None = None,
            campaigns_filter: list[str] | None = None, search: str | None = None,
            entity_type: str | list[str] | None = None, field: str | None = None,
            field_group: str | None = None) -> list[dict[str, Any]]:
    # Older Amazon history rows can contain the campaign ID as the object's label.
    # Never use the parent campaign's name as the name of a missing child.
    where, params = _camp_filter(campaigns_filter, 'v.campaign_id')
    for column, op, value in [('change_date', '>=', start), ('change_date', '<=', end),
                              ('field', '=', field)]:
        if value:
            where += f' AND v.{column} {op} ?'
            params.append(value)
    types = [entity_type] if isinstance(entity_type, str) else list(entity_type or [])
    types = [t for t in types if t]
    if types:
        where += f" AND v.entity_type IN ({','.join('?' for _ in types)})"
        params += types
    if field_group:
        group = FIELD_GROUPS.get(field_group)
        if group is None:
            raise ValueError(f"неизвестная группа полей: {field_group}")
        where += f" AND v.field IN ({','.join('?' for _ in group)})"
        params += list(group)
    rows = conn.execute(f"""
        WITH v AS (
            SELECT ch.change_id, ch.change_date, ch.change_ts, ch.date_precision, ch.entity_type,
                   ch.entity_id, ch.ad_group_id, ch.field, ch.old_value, ch.new_value, ch.delta,
                   ch.source, ch.actor, ch.summary,
                   COALESCE(NULLIF(ch.campaign_id,''), CASE WHEN ch.entity_type='CAMPAIGN'
                            THEN ch.entity_id END, t.campaign_id, g.campaign_id, a.campaign_id) campaign_id,
                   ch.entity_label saved_label, COALESCE(NULLIF(g.name,''), gr.name, '') ad_group_name,
                   COALESCE(NULLIF(t.targeting,''),NULLIF(g.name,''),
                                                        NULLIF(a.asin,''),NULLIF(a.sku,'')) object_name
            FROM changes ch
            LEFT JOIN dim_target t ON ch.entity_type IN {TARGET_TYPES} AND t.target_id=ch.entity_id
                AND (NULLIF(ch.campaign_id,'') IS NULL OR t.campaign_id=ch.campaign_id)
            LEFT JOIN dim_adgroup g ON ch.entity_type='AD_GROUP' AND g.ad_group_id=ch.entity_id
                AND (NULLIF(ch.campaign_id,'') IS NULL OR g.campaign_id=ch.campaign_id)
            LEFT JOIN dim_adgroup gr ON gr.ad_group_id=ch.ad_group_id
            LEFT JOIN dim_ad a ON ch.entity_type='AD' AND a.ad_id=ch.entity_id
                AND (NULLIF(ch.campaign_id,'') IS NULL OR a.campaign_id=ch.campaign_id)
        )
        SELECT v.*, COALESCE(NULLIF(c.name,''),v.campaign_id) campaign_name,
               COALESCE(NULLIF(NULLIF(NULLIF(v.saved_label,''),v.campaign_id),v.entity_id),
                        CASE WHEN v.entity_type='CAMPAIGN' THEN NULLIF(c.name,'') END,
                        v.object_name, v.entity_id) entity_label
        FROM v LEFT JOIN dim_campaign c ON c.campaign_id=v.campaign_id
        WHERE 1=1 {where}
        ORDER BY v.change_date DESC, julianday(v.change_ts) DESC, v.change_id DESC""", params)
    result = []
    for row in rows:
        if search and not any(search.lower() in (row[key] or '').lower()
                              for key in ('campaign_name', 'entity_label', 'summary', 'field')):
            continue
        item = {k: row[k] for k in row.keys() if k not in ('saved_label', 'object_name')}
        if item["ad_group_name"] == item["ad_group_id"]:
            item["ad_group_name"] = ""
        if item["entity_type"] in TARGET_TYPES and item["entity_label"]:
            item["entity_label"] = targeting_label(item["entity_label"])
        result.append(item)
    return result


def meta(conn: sqlite3.Connection) -> dict[str, Any]:
    campaigns_list = conn.execute(
        "SELECT campaign_id, name, state, budget FROM dim_campaign ORDER BY name"
    ).fetchall()
    span = conn.execute("SELECT MIN(date), MAX(date) FROM fact_campaign_day").fetchone()
    last_run = conn.execute(
        "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("fact_campaign_day", "fact_target_day", "fact_searchterm_day",
                      "fact_placement_day", "fact_product_day", "changes")
    }
    changes_span = conn.execute("SELECT MIN(change_date) FROM changes").fetchone()
    return {
        "campaigns": [dict(r) for r in campaigns_list],
        "changes_min": changes_span[0],
        "date_min": span[0],
        "date_max": span[1],
        "last_run": dict(last_run) if last_run else None,
        "counts": counts,
        "targets": conn.execute(
            "SELECT COUNT(*) FROM dim_target WHERE is_negative=0"
        ).fetchone()[0],
        "negatives": conn.execute(
            "SELECT COUNT(*) FROM dim_target WHERE is_negative=1"
        ).fetchone()[0],
    }


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT dataset, date, rows, spend, status FROM coverage ORDER BY date DESC, dataset"
    ).fetchall()
    runs = conn.execute(
        "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 20"
    ).fetchall()
    return {"coverage": [dict(r) for r in rows], "runs": [dict(r) for r in runs]}
