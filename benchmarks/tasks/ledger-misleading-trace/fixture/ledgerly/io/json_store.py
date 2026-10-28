"""The ledger file: accounts, rules and transactions in one JSON document.

Writes go to a temporary file that replaces the old one, so a crash never leaves half a ledger.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ledgerly.core.accounts import Account, Chart
from ledgerly.core.errors import LedgerError
from ledgerly.core.journal import Journal
from ledgerly.core.transactions import Transaction
from ledgerly.rules.categorize import Rule

FORMAT_VERSION = 2


def load(path: Path) -> tuple[Journal, list[Rule]]:
    """The journal and rules in ``path``; an empty ledger (default categories) if it is missing."""

    if not path.exists():
        return Journal(Chart()), []
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError(f"cannot read the ledger {path}: {exc}") from None
    version = data.get("version")
    if version == 1:
        data = upgrade_v1(data)
    elif version != FORMAT_VERSION:
        raise LedgerError(f"{path} is ledger format {version!r}; this ledgerly reads 1 and 2")
    chart = Chart([Account.from_dict(item) for item in data.get("accounts", [])])
    journal = Journal(chart)
    journal.extend(Transaction.from_dict(item) for item in data.get("transactions", []))
    rules = [Rule.from_dict(item) for item in data.get("rules", [])]
    return journal, rules


def save(path: Path, journal: Journal, rules: list[Rule]) -> None:
    document = {
        "version": FORMAT_VERSION,
        "accounts": [a.to_dict() for a in journal.chart.all()],
        "rules": [r.to_dict() for r in rules],
        "transactions": [t.to_dict() for t in journal],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ledger-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(document, out, indent=1, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def upgrade_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Format 1 kept categories as plain strings and had no rules."""

    accounts = list(data.get("accounts", []))
    known = {item["id"] for item in accounts}
    for name in data.get("categories", []):
        if name not in known:
            accounts.append({"id": name, "name": name.replace("-", " ").title(), "kind": "expense"})
    return {
        "version": FORMAT_VERSION,
        "accounts": accounts,
        "rules": [],
        "transactions": data.get("transactions", []),
    }
