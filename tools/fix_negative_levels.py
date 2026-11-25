"""Привести уровень минус-таргетов в базе к тому, что загружает текущий код.

Кампанийный минус-таргет приходит из Amazon без adGroupId. До исправления в
`ingest/detect.py` такой файл распознавался как `negative_targets`, и уровень
в базе записывался как AD_GROUP. Повторный импорт эти строки уже не чинит:
`negative_level` не входит в `fields` спеки загрузчика, поэтому апсерт его не
трогает. Отсюда отдельная операция — разовая и идемпотентная.

    python3 tools/fix_negative_levels.py --check          # только посчитать
    python3 tools/fix_negative_levels.py --apply          # исправить
    python3 tools/fix_negative_levels.py --check --db data/accounts/dimon.db

Код возврата: 0 — расхождений нет (или всё исправлено), 1 — расхождения есть
в режиме `--check`, 2 — базы нет или аргументы противоречат друг другу.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Минус без группы — это минус уровня кампании. Ровно то же правило, что и в
# ingest/detect.py: negative_targets с adGroupId, campaign_negative_targets без.
BROKEN = (
    "is_negative=1 AND COALESCE(ad_group_id,'')='' "
    "AND COALESCE(negative_level,'')<>'CAMPAIGN'"
)


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Только чтение: --check не должен уметь менять базу даже случайно."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def broken_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        f"SELECT target_id, campaign_id, kind, targeting, negative_level "
        f"FROM dim_target WHERE {BROKEN} ORDER BY campaign_id, target_id"
    ))


def report(rows: list[sqlite3.Row]) -> None:
    by_campaign: dict[str, int] = {}
    for row in rows:
        by_campaign[row["campaign_id"]] = by_campaign.get(row["campaign_id"], 0) + 1
    print(f"минус-таргетов без группы с уровнем AD_GROUP: {len(rows)}")
    for campaign_id, count in sorted(by_campaign.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  кампания {campaign_id}: {count}")
    if rows:
        sample = rows[0]
        print(f"  пример: {sample['target_id']} · {sample['kind']} · {sample['targeting']}")


def apply(conn: sqlite3.Connection) -> int:
    """Одна транзакция: либо исправлены все найденные строки, либо ни одной."""
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        found = len(broken_rows(conn))
        changed = conn.execute(
            f"UPDATE dim_target SET negative_level='CAMPAIGN' WHERE {BROKEN}"
        ).rowcount
        if changed != found:
            raise RuntimeError(f"ожидалось {found} строк, изменено {changed}")
        left = len(broken_rows(conn))
        if left:
            raise RuntimeError(f"после UPDATE осталось {left} строк")
        conn.execute("COMMIT")
        return changed
    except Exception:
        conn.execute("ROLLBACK")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Минус-таргеты без adGroupId: уровень CAMPAIGN вместо AD_GROUP")
    parser.add_argument("--db", help="путь к базе; по умолчанию база кабинета по умолчанию")
    parser.add_argument("--account", help="id кабинета из data/accounts.json")
    parser.add_argument("--check", action="store_true", help="только показать (режим по умолчанию)")
    parser.add_argument("--apply", action="store_true", help="исправить найденные строки")
    args = parser.parse_args(argv)

    if args.check and args.apply:
        print("--check и --apply вместе не имеют смысла", file=sys.stderr)
        return 2
    if args.db and args.account:
        print("--db и --account взаимоисключающие", file=sys.stderr)
        return 2

    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        sys.path.insert(0, str(ROOT))
        from app.config import settings  # импорт здесь: --db не должен требовать конфиг

        accounts = settings.accounts()
        account_id = args.account or settings.default_account
        if account_id not in accounts:
            print(f"нет кабинета {account_id}; есть: {', '.join(accounts)}", file=sys.stderr)
            return 2
        db_path = accounts[account_id].db_path

    if not db_path.is_file():
        print(f"нет базы: {db_path}", file=sys.stderr)
        return 2

    if args.apply:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            rows = broken_rows(conn)
            report(rows)
            if not rows:
                print("исправлять нечего")
                return 0
            print(f"датасет: {db_path}")
            changed = apply(conn)
            print(f"исправлено: {changed}")
            return 0
        except sqlite3.Error as exc:
            print(f"ошибка базы: {exc}", file=sys.stderr)
            return 2
        finally:
            conn.close()

    conn = connect_readonly(db_path)
    try:
        rows = broken_rows(conn)
    finally:
        conn.close()
    report(rows)
    if rows:
        print(f"база: {db_path}")
        print(f"расхождений: {len(rows)}. Исправить: --apply")
        return 1
    print(f"база: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())