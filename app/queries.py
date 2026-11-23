"""Аналитические запросы к базе дашборда."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

METRICS = ("impressions", "clicks", "spend", "orders", "sales", "units")


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
            search: str | None = None, limit: int = 2000) -> list[dict[str, Any]]:
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
                   COALESCE(f.impressions,0) impressions, COALESCE(f.clicks,0) clicks,
                   COALESCE(f.spend,0) spend, COALESCE(f.orders,0) orders,
                   COALESCE(f.sales,0) sales, COALESCE(f.units,0) units
            FROM dim_target t
            LEFT JOIN dim_campaign c ON c.campaign_id=t.campaign_id
            LEFT JOIN dim_adgroup g ON g.ad_group_id=t.ad_group_id
            LEFT JOIN (SELECT campaign_id, ad_group_id, target_id,
                              SUM(impressions) impressions, SUM(clicks) clicks, SUM(spend) spend,
                              SUM(orders) orders, SUM(sales) sales, SUM(units) units
                       FROM fact_target_day WHERE date BETWEEN ? AND ?
                       GROUP BY campaign_id, ad_group_id, target_id) f
                   ON f.campaign_id=t.campaign_id AND f.ad_group_id=t.ad_group_id AND f.target_id=t.target_id
            WHERE 1=1 {where}
            ORDER BY t.is_negative, COALESCE(f.spend,0) DESC, t.targeting
            LIMIT {int(limit)}""",
        [start, end, *params],
    ).fetchall()
    return [with_ratios(dict(r)) for r in rows]


def search_terms(conn: sqlite3.Connection, start: str, end: str,
                 campaigns_filter: list[str] | None, search: str | None = None,
                 only_orders: bool = False, limit: int = 500) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter)
    if search:
        where += " AND search_term LIKE ?"
        params.append(f"%{search}%")
    having = " HAVING SUM(orders) > 0" if only_orders else ""
    rows = conn.execute(
        f"""SELECT search_term, campaign_id, ad_group_id, target_id, targeting, match_type,
                   SUM(impressions) impressions, SUM(clicks) clicks, SUM(spend) spend,
                   SUM(orders) orders, SUM(sales) sales, SUM(units) units
            FROM fact_searchterm_day WHERE date BETWEEN ? AND ? {where}
            GROUP BY search_term, campaign_id {having}
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
    return [with_ratios(dict(r)) for r in rows]


def products(conn: sqlite3.Connection, start: str, end: str,
             campaigns_filter: list[str] | None) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter)
    rows = conn.execute(
        f"""SELECT asin, marketplace, campaign_id, ad_id,
                   SUM(impressions) impressions, SUM(clicks) clicks, SUM(spend) spend,
                   SUM(orders) orders, SUM(sales) sales, SUM(units) units
            FROM fact_product_day WHERE date BETWEEN ? AND ? {where}
            GROUP BY asin, marketplace, campaign_id ORDER BY spend DESC""",
        [start, end, *params],
    ).fetchall()
    return [with_ratios(dict(r)) for r in rows]


def changes(conn: sqlite3.Connection, start: str, end: str,
            campaigns_filter: list[str] | None, search: str | None = None,
            limit: int = 2000) -> list[dict[str, Any]]:
    where, params = _camp_filter(campaigns_filter)
    if search:
        where += " AND (summary LIKE ? OR field LIKE ? OR entity_label LIKE ?)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    rows = conn.execute(
        f"""SELECT change_id, change_date, change_ts, date_precision, entity_type, entity_id,
                   campaign_id, ad_group_id, entity_label, field, old_value, new_value, delta,
                   source, actor, summary
            FROM changes WHERE change_date BETWEEN ? AND ? {where}
            ORDER BY change_ts DESC, change_date DESC LIMIT {int(limit)}""",
        [start, end, *params],
    ).fetchall()
    return [dict(r) for r in rows]


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
    return {
        "campaigns": [dict(r) for r in campaigns_list],
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
