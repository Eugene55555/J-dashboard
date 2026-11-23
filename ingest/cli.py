"""Распознать папку и загрузить отчёты в базу.

Использование:
    python -m ingest.cli scan  ./inbox
    python -m ingest.cli load  ./inbox --db data/dashboard.db
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .detect import scan_folder
from .load import connect, import_folder, start_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка отчётов Amazon Ads в SQLite")
    parser.add_argument("command", choices=("scan", "load"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--db", default="data/dashboard.db")
    args = parser.parse_args()

    if args.command == "scan":
        for item in scan_folder(args.folder):
            kind = ",".join(item["kind"]) if isinstance(item["kind"], list) else item["kind"]
            print(f"{item['category']:10s} {str(kind):40s} {len(item['rows']):6d}  {item['path'].name}")
        return

    conn = connect(args.db)
    run_id = "import-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    start_run(conn, run_id, "folder")
    result = import_folder(conn, args.folder, run_id)
    print(json.dumps({"run_id": run_id, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
