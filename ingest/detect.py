"""Классификация файлов Amazon Ads API: что это за отчёт и какое у него зерно.

Правило: пользователь просто кидает скачанные отчёты в папку, а распознавание
идёт по составу полей, а не по имени файла.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

# Поля отчёта Reporting v1.
REPORT_MARKERS = ("date.value", "campaign.id")

DATASETS = (
    "campaign_day",
    "adgroup_day",
    "target_day",
    "searchterm_day",
    "placement_day",
    "product_day",
)


def activity_datasets(keys: set[str]) -> list[str]:
    """Все датасеты, которые можно собрать из этого отчёта.

    Отчёт уровня кампании идёт только в campaign_day: детальный отчёт теряет
    часть показов, поэтому смешивать источники в дневных итогах кампаний нельзя.
    """
    if "adGroup.id" not in keys:
        return ["campaign_day"]
    datasets = ["adgroup_day"]
    if "adGroupTarget.id" in keys:
        datasets.append("target_day")
    if "searchTerm.value" in keys:
        datasets.append("searchterm_day")
    if "placementClassification.value" in keys:
        datasets.append("placement_day")
    if "ad.id" in keys and "advertisedProduct.id" in keys:
        datasets.append("product_day")
    return datasets


def settings_kind(row: dict[str, Any]) -> str | None:
    """Определить тип объекта настроек по полям строки."""
    keys = set(row)
    if "adId" in keys:
        return "ads"
    if "defaultBid" in keys:
        return "adgroups"
    if "budget" in keys or ("campaignId" in keys and "targetingType" in keys):
        return "campaigns"
    if "targetId" in keys:
        return "negative_targets" if "bid" not in keys else "targets"
    if "keywordId" in keys:
        match = str(row.get("matchType") or "").upper()
        negative = match.startswith("NEGATIVE") or "negativeKeywordId" in keys
        if negative:
            return "negative_keywords" if row.get("adGroupId") else "campaign_negative_keywords"
        return "keywords"
    return None


def detect_rows(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Вернуть (категория, датасет/вид) для массива однотипных строк."""
    if not rows:
        return None
    sample = rows[0]
    keys = set(sample)
    if "changeType" in keys or ("change_id" in keys and "field" in keys):
        return ("changes", "changes")
    if all(marker in keys for marker in REPORT_MARKERS):
        return ("activity", activity_datasets(keys))
    kind = settings_kind(sample)
    if kind:
        category = "negative" if "negative" in kind else "settings"
        return (category, kind)
    return None


def read_json(path: Path) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return None
    if isinstance(payload, dict):
        # Иногда отчёт приходит как {"data": [...]} — поддержим частые обёртки.
        for key in ("rows", "data", "results", "report"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        return None
    if isinstance(payload, list) and all(isinstance(r, dict) for r in payload):
        return payload
    return None


def read_csv(path: Path) -> list[dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        sample = text[:4096]
        if not sample.strip():
            return None
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = [dict(r) for r in reader]
    except csv.Error:
        return None
    return rows or None


def scan_folder(folder: Path) -> list[dict[str, Any]]:
    """Пройти папку и вернуть найденные источники с категорией."""
    found: list[dict[str, Any]] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix not in (".json", ".csv"):
            continue
        rows = read_json(path) if suffix == ".json" else read_csv(path)
        if not rows:
            continue
        detected = detect_rows(rows)
        if not detected:
            found.append({"path": path, "category": "unknown", "kind": None, "rows": rows})
            continue
        found.append({"path": path, "category": detected[0], "kind": detected[1], "rows": rows})
    return found
