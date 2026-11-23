"""Собрать статический сайт для GitHub Pages из базы дашборда.

    python3 build_static.py --account eugene --out docs

Скрипт не меняет рекламу и не ходит в Amazon: он только читает SQLite и
раскладывает данные в docs/data.json вместе с готовым интерфейсом.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from app.config import ROOT, settings
from ingest.load import connect

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:  # локальная сборка без шифрования остаётся возможной
    AESGCM = None

STATIC_SRC = ROOT / "app" / "static"
PAGES = ("index.html", "style.css", "app.js", "local.js")

GATE_HTML = """<div class="gate hidden" id="gate">
  <form class="gate-card" id="gate-form" autocomplete="on">
    <div class="brand">
      <div class="brand-mark">J</div>
      <div><div class="brand-name">J-dashboard</div>
        <div class="brand-sub">Amazon Ads · приватный доступ</div></div>
    </div>
    <label for="gate-login">Логин</label>
    <input id="gate-login" type="text" autocapitalize="none" autocomplete="username">
    <label for="gate-password">Пароль</label>
    <input id="gate-password" type="password" autocomplete="current-password">
    <button class="btn primary" type="submit" id="gate-btn">Войти</button>
    <div class="gate-err" id="gate-error"></div>
  </form>
</div>"""
ITERATIONS = 200_000


def encrypt_payload(payload: dict, password: str, login: str) -> dict:
    """Зашифровать data.json паролем; ключ = PBKDF2(login + \0 + password)."""
    if AESGCM is None:
        raise SystemExit("нужен пакет cryptography: pip install cryptography")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    salt, nonce = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERATIONS).derive((login + "\x00" + password).encode())
    ciphertext = AESGCM(key).encrypt(nonce, raw, None)
    return {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iter": ITERATIONS,
        "login": login,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ciphertext).decode(),
    }


def rows(conn, sql: str) -> list[dict]:
    return [dict(r) for r in conn.execute(sql)]


def build(account_id: str, out_dir: Path, password: str = "", login: str = "eugene") -> dict:
    accounts = settings.accounts()
    if account_id not in accounts:
        raise SystemExit(f"нет кабинета {account_id}; есть: {', '.join(accounts)}")
    account = accounts[account_id]
    conn = connect(account.db_path)

    campaigns = rows(conn, """
        SELECT campaign_id, name, state, targeting_type, budget, budget_type, bid_strategy,
               adj_top, adj_product_page, adj_rest, start_date, end_date, serving_status, updated_at
        FROM dim_campaign ORDER BY name""")
    adgroups = rows(conn, "SELECT ad_group_id, campaign_id, name FROM dim_adgroup")
    targets = rows(conn, """
        SELECT target_id, campaign_id, ad_group_id, targeting, match_type, is_negative,
               negative_level, state, bid, effective_bid, serving_status, updated_at
        FROM dim_target""")

    facts = {}
    facts["campaign_day"] = [list(r) for r in conn.execute("""
        SELECT date, campaign_id, impressions, clicks, spend, orders, sales, units
        FROM fact_campaign_day ORDER BY date, campaign_id""")]
    facts["target_day"] = [list(r) for r in conn.execute("""
        SELECT date, campaign_id, ad_group_id, target_id, impressions, clicks, spend, orders, sales, units
        FROM fact_target_day ORDER BY date""")]
    facts["searchterm_day"] = [list(r) for r in conn.execute("""
        SELECT date, campaign_id, ad_group_id, target_id, search_term, targeting, match_type,
               impressions, clicks, spend, orders, sales, units
        FROM fact_searchterm_day ORDER BY date""")]
    facts["placement_day"] = [list(r) for r in conn.execute("""
        SELECT date, campaign_id, placement, impressions, clicks, spend, orders, sales, units
        FROM fact_placement_day ORDER BY date""")]
    facts["product_day"] = [list(r) for r in conn.execute("""
        SELECT date, campaign_id, ad_group_id, ad_id, asin, marketplace,
               impressions, clicks, spend, orders, sales, units
        FROM fact_product_day ORDER BY date""")]

    changes = rows(conn, """
        SELECT change_date, change_ts, date_precision, entity_type, entity_id, campaign_id,
               ad_group_id, entity_label, field, old_value, new_value, delta, source, actor, summary
        FROM changes ORDER BY change_ts DESC, change_date DESC LIMIT 5000""")
    coverage = rows(conn, "SELECT dataset, date, rows, spend, status FROM coverage ORDER BY date DESC")
    runs = rows(conn, """SELECT run_id, started_at, finished_at, status, files, rows, source
                         FROM sync_runs ORDER BY started_at DESC LIMIT 20""")

    span = conn.execute("SELECT MIN(date), MAX(date) FROM fact_campaign_day").fetchone()
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("fact_campaign_day", "fact_target_day", "fact_searchterm_day",
                      "fact_placement_day", "fact_product_day", "changes")
    }
    positives = conn.execute("SELECT COUNT(*) FROM dim_target WHERE is_negative=0").fetchone()[0]
    negatives = conn.execute("SELECT COUNT(*) FROM dim_target WHERE is_negative=1").fetchone()[0]
    conn.close()

    payload = {
        "meta": {
            "account": {
                "id": account.id,
                "name": account.label,
                "profile_id": account.profile_id,
                "currency": account.currency,
                "timezone": account.timezone,
                "data_through": span[1],
            },
            "date_min": span[0],
            "date_max": span[1],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": counts,
            "targets": positives,
            "negatives": negatives,
        },
        "campaigns": campaigns,
        "adgroups": adgroups,
        "targets": targets,
        "facts": facts,
        "changes": changes,
        "coverage": coverage,
        "runs": runs,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    assets = out_dir / "static"
    assets.mkdir(exist_ok=True)
    (assets / "style.css").write_text((STATIC_SRC / "style.css").read_text(encoding="utf-8"), encoding="utf-8")
    (assets / "app.js").write_text((STATIC_SRC / "app.js").read_text(encoding="utf-8"), encoding="utf-8")
    (assets / "local.js").write_text((STATIC_SRC / "local.js").read_text(encoding="utf-8"), encoding="utf-8")

    html = (STATIC_SRC / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/style.css"', 'href="static/style.css"')
    html = html.replace('<script src="/static/app.js"></script>',
                        '<script>window.J_STATIC = { dataUrl: "data.json" };</script>\n'
                        '<script src="static/local.js"></script>\n'
                        '<script src="static/app.js"></script>')
    html = html.replace('<meta name="viewport"',
                        '<meta name="robots" content="noindex, nofollow">\n<meta name="viewport"')
    if password:
        html = html.replace('window.J_STATIC = { dataUrl: "data.json" };',
                            'window.J_STATIC = { dataUrl: "data.enc.json", encrypted: true, login: "%s" };' % login)
        html = html.replace('<div class="shell">', GATE_HTML + '\n<div class="shell">')
    assert "J_STATIC" in html and 'href="static/style.css"' in html
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    plain_path = out_dir / "data.json"
    enc_path = out_dir / "data.enc.json"
    if password:
        enc_path.write_text(json.dumps(encrypt_payload(payload, password, login), ensure_ascii=False),
                            encoding="utf-8")
        if plain_path.exists():
            plain_path.unlink()  # открытые данные не должны оставаться рядом
        data_path = enc_path
    else:
        plain_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                              encoding="utf-8")
        if enc_path.exists():
            enc_path.unlink()
        data_path = plain_path
    return {
        "account": account.id,
        "out": str(out_dir),
        "encrypted": bool(password),
        "data_mb": round(data_path.stat().st_size / 1024 / 1024, 2),
        "campaign_days": len(facts["campaign_day"]),
        "target_days": len(facts["target_day"]),
        "terms": len(facts["searchterm_day"]),
        "changes": len(changes),
        "span": [span[0], span[1]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Статическая сборка дашборда для GitHub Pages")
    parser.add_argument("--account", default=settings.default_account)
    parser.add_argument("--out", default="docs")
    parser.add_argument("--password", default="", help="включить вход по паролю (шифрование data.json)")
    parser.add_argument("--login", default="eugene", help="логин для страницы входа")
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    print(json.dumps(build(args.account, out, args.password, args.login),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
