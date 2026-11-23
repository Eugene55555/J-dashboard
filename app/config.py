"""Настройки приложения из переменных окружения и accounts.json."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "да")


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    db_path: Path
    currency: str = "USD"
    profile_id: str = ""
    timezone: str = "America/Los_Angeles"


@dataclass(frozen=True)
class Settings:
    inbox: Path = field(default_factory=lambda: Path(os.getenv("J_INBOX", ROOT / "data" / "inbox")))
    inbox_root: Path = field(default_factory=lambda: Path(os.getenv("J_INBOX_ROOT", ROOT / "data")))
    accounts_file: Path = field(default_factory=lambda: Path(os.getenv("J_ACCOUNTS", ROOT / "data" / "accounts.json")))
    password: str = field(default_factory=lambda: os.getenv("J_PASSWORD", ""))
    secret: str = field(default_factory=lambda: os.getenv("J_SECRET") or secrets.token_hex(32))
    secure_cookie: bool = field(default_factory=lambda: _bool(os.getenv("J_SECURE_COOKIE")))
    account_name: str = field(default_factory=lambda: os.getenv("J_ACCOUNT_NAME", "Amazon Ads"))
    profile_id: str = field(default_factory=lambda: os.getenv("J_PROFILE_ID", ""))
    currency: str = field(default_factory=lambda: os.getenv("J_CURRENCY", "USD"))
    account_timezone: str = field(default_factory=lambda: os.getenv("J_TZ", "America/Los_Angeles"))
    default_account_id: str = field(default_factory=lambda: os.getenv("J_ACCOUNT", ""))

    def accounts(self) -> dict[str, Account]:
        """Все кабинеты. Без accounts.json — один кабинет из переменных окружения."""
        if self.accounts_file.exists():
            raw = json.loads(self.accounts_file.read_text(encoding="utf-8"))
            accounts: dict[str, Account] = {}
            for item in raw:
                account_id = str(item["id"])
                db_path = Path(item.get("db") or f"data/{account_id}.db")
                if not db_path.is_absolute():
                    db_path = ROOT / db_path
                accounts[account_id] = Account(
                    id=account_id,
                    label=item.get("label") or account_id,
                    db_path=db_path,
                    currency=item.get("currency") or self.currency,
                    profile_id=str(item.get("profile_id") or ""),
                    timezone=item.get("timezone") or self.account_timezone,
                )
            if accounts:
                return accounts
        db_path = Path(os.getenv("J_DB", ROOT / "data" / "dashboard.db"))
        return {
            "default": Account(
                id="default",
                label=self.account_name,
                db_path=db_path,
                currency=self.currency,
                profile_id=self.profile_id,
                timezone=self.account_timezone,
            )
        }

    @property
    def default_account(self) -> str:
        accounts = self.accounts()
        if self.default_account_id in accounts:
            return self.default_account_id
        return next(iter(accounts))


settings = Settings()
