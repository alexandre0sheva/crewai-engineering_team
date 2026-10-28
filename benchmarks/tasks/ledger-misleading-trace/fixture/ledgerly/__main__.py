"""The ``ledgerly`` command line (``python -m ledgerly``).

    ledgerly [--ledger FILE] [--rates FILE] COMMAND ...

    account add ID NAME [--kind asset|liability] [--currency EUR] [--iban IBAN]
    accounts
    import FILE --format generic|ing|rabo --account ID
    report income PERIOD [--compare]
    report balance [--on YYYY-MM-DD]
    report budget PERIOD --budgets FILE
    export [PERIOD]                CSV on standard output
    rule add CATEGORY MATCHER... [--income-only|--expense-only] [--name NAME]
    explain SOURCE                 which rule categorised the transaction from FILE:LINE

Errors print ``error: <message>`` and exit with status 2.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from ledgerly.core.accounts import Account, Kind
from ledgerly.core.errors import LedgerError
from ledgerly.core.periods import parse_period
from ledgerly.fx.rates import load_rates
from ledgerly.io import json_store
from ledgerly.io.csv_import import import_statement
from ledgerly.io.export import export_csv
from ledgerly.reports.balance import balances
from ledgerly.reports.budget import compare, load_budgets, render_budget
from ledgerly.reports.income import income_statement
from ledgerly.reports.render import render_balances, render_income
from ledgerly.rules.categorize import Categorizer, Rule
from ledgerly.rules.matchers import parse_matcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerly", description="A small personal ledger.")
    parser.add_argument("--ledger", default="ledger.json", type=Path)
    parser.add_argument("--rates", default="rates.csv", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    account = commands.add_parser("account").add_subparsers(dest="action", required=True)
    add = account.add_parser("add")
    add.add_argument("id")
    add.add_argument("name")
    add.add_argument("--kind", choices=("asset", "liability"), default="asset")
    add.add_argument("--currency", default="EUR")
    add.add_argument("--iban")

    commands.add_parser("accounts")

    load = commands.add_parser("import")
    load.add_argument("file", type=Path)
    load.add_argument("--format", required=True, choices=("generic", "ing", "rabo"))
    load.add_argument("--account", required=True)

    report = commands.add_parser("report").add_subparsers(dest="report", required=True)
    income = report.add_parser("income")
    income.add_argument("period")
    income.add_argument("--compare", action="store_true")
    balance = report.add_parser("balance")
    balance.add_argument("--on", type=date.fromisoformat, default=None)
    budget = report.add_parser("budget")
    budget.add_argument("period")
    budget.add_argument("--budgets", required=True, type=Path)

    export = commands.add_parser("export")
    export.add_argument("period", nargs="?")

    rule = commands.add_parser("rule").add_subparsers(dest="action", required=True)
    rule_add = rule.add_parser("add")
    rule_add.add_argument("category")
    rule_add.add_argument("matchers", nargs="+")
    only = rule_add.add_mutually_exclusive_group()
    only.add_argument("--income-only", action="store_true")
    only.add_argument("--expense-only", action="store_true")
    rule_add.add_argument("--name", default="")

    explain = commands.add_parser("explain")
    explain.add_argument("source")
    return parser


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    journal, rules = json_store.load(args.ledger)
    changed = False
    if args.command == "account":
        journal.chart.add(
            Account(args.id, args.name, Kind(args.kind), args.currency.upper(), args.iban)
        )
        changed = True
        print(f"added account {args.id}")
    elif args.command == "accounts":
        for account in journal.chart.all():
            iban = f"  {account.iban}" if account.iban else ""
            print(f"{account.kind.value:<10} {account.id:<16} {account.name}{iban}")
    elif args.command == "import":
        result = import_statement(
            journal, args.file, args.format, args.account, Categorizer(rules)
        )
        changed = result.added > 0
        print(result.summary())
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    elif args.command == "report":
        rates = load_rates(args.rates)
        if args.report == "income":
            statement = income_statement(
                journal, parse_period(args.period), rates, compare=args.compare
            )
            sys.stdout.write(render_income(statement, compare=args.compare))
        elif args.report == "budget":
            period = parse_period(args.period)
            lines = compare(journal, period, rates, load_budgets(args.budgets, journal))
            sys.stdout.write(render_budget(lines, period))
        else:
            day = args.on or (journal.first_and_last() or (date.today(), date.today()))[1]
            sys.stdout.write(render_balances(balances(journal, rates, day), rates.home))
    elif args.command == "rule":
        if args.category not in journal.chart:
            raise LedgerError(f"no category {args.category!r}")
        rules.append(
            Rule(
                args.category,
                [parse_matcher(text) for text in args.matchers],
                name=args.name,
                income_only=args.income_only,
                expense_only=args.expense_only,
            )
        )
        changed = True
        print(f"rule added for {args.category}")
    elif args.command == "export":
        period = parse_period(args.period) if args.period else None
        sys.stdout.write(export_csv(journal, period))
    elif args.command == "explain":
        categorizer = Categorizer(rules)
        matches = [t for t in journal if t.source == args.source]
        if not matches:
            raise LedgerError(f"no transaction was imported from {args.source}")
        for transaction in matches:
            print(categorizer.explain(transaction))
    if changed:
        json_store.save(args.ledger, journal, rules)
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
