"""Сверка и перенос общей части двух репозиториев кабинета.

J-dashboard — источник правды для общего кода; D-dashboard отличается только
брендом, данными и собранной публикацией. Скрипт сравнивает общие файлы,
показывает расхождения и (с --apply) переносит их из источника в цель.

    python3 tools/sync_shared.py --check
    python3 tools/sync_shared.py --apply

Код возврата: 0 — расхождений нет (или всё перенесено), 1 — есть расхождения
в режиме --check, 2 — нет каталога.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Общий код: должен совпадать байт в байт в обоих репозиториях.
SHARED = (
    "app/config.py",
    "app/main.py",
    "app/queries.py",
    "app/static/app.js",
    "app/static/local.js",
    "app/static/style.css",
    "ingest/amazon.py",
    "ingest/cli.py",
    "ingest/detect.py",
    "ingest/history.py",
    "ingest/load.py",
    "ingest/schema.sql",
    "tests/campaign-picker.browser.cjs",
    "tests/test_coverage.py",
    "tests/test_history_pull.py",
    "tests/test_history_view.py",
    "tests/test_ingest.py",
    "tests/test_login_throttle.py",
    "tests/test_settings_views.py",
    "tests/test_static_payload.py",
    "tests/test_sync_shared.py",
    "tests/test_targets_view.py",
    "tests/test_ui_build.py",
    "tests/ui-filters.test.cjs",
    "tests/ui-unlock.test.cjs",
    "tests/ui.test.cjs",
    "tools/sync_shared.py",
)

# Расходится намеренно: бренд, реквизиты аккаунта, сборка публикации.
BRAND = (
    "app/static/index.html",
    "app/static/login.html",
    "app/static/favicon.svg",
    "build_static.py",
    "README.md",
    "accounts.example.json",
    ".env.example",
)


def digest(path: Path) -> str:
    """Короткий отпечаток файла; отсутствие файла — тоже состояние."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else "нет файла"


def compare(source: Path, target: Path) -> list[tuple[str, str, str]]:
    drift = []
    for rel in SHARED:
        left, right = digest(source / rel), digest(target / rel)
        if left != right:
            drift.append((rel, left, right))
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка общей части J-dashboard и D-dashboard")
    parser.add_argument("--source", default=str(ROOT.parent / "J-dashboard"),
                        help="репозиторий-источник (по умолчанию соседний J-dashboard)")
    parser.add_argument("--target", default=str(ROOT.parent / "D-dashboard"),
                        help="репозиторий-цель (по умолчанию соседний D-dashboard)")
    parser.add_argument("--check", action="store_true",
                        help="только сверить, ничего не менять (режим по умолчанию)")
    parser.add_argument("--apply", action="store_true",
                        help="перенести общие файлы из источника в цель")
    args = parser.parse_args(argv)

    source, target = Path(args.source).resolve(), Path(args.target).resolve()
    for path in (source, target):
        if not path.is_dir():
            print(f"нет каталога: {path}", file=sys.stderr)
            return 2

    if args.check and args.apply:
        print("--check и --apply вместе не имеют смысла", file=sys.stderr)
        return 2

    drift = compare(source, target)
    if not drift:
        print(f"общий код совпадает: {len(SHARED)} файлов")
        return 0

    for rel, left, right in drift:
        print(f"разошлось: {rel} ({left} ≠ {right})")
    if not args.apply:
        print(f"расхождений: {len(drift)}. Перенести: --apply")
        return 1

    copied, missing = 0, 0
    for rel, _, _ in drift:
        src, dst = source / rel, target / rel
        if src.resolve() == dst.resolve():
            continue
        if not src.is_file():
            print(f"нет в источнике, пропущено: {rel}")
            missing += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f"обновлено: {rel}")
    print(f"перенесено: {copied}" + (f", пропущено: {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())