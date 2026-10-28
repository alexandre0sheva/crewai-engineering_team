"""Account balances: the sum of each money account's transactions up to a day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ledgerly.core.journal import Journal
from ledgerly.core.money import Money
from ledgerly.fx.convert import to_home
from ledgerly.fx.rates import RateTable


@dataclass
class Balance:
    account: str
    name: str
    own: Money  # in the account's own currency
    home: Money  # converted on the day


def balances(journal: Journal, rates: RateTable, on: date) -> list[Balance]:
    found = []
    for account_id in journal.money_accounts():
        account = journal.chart.get(account_id)
        amount = Decimal("0")
        for transaction in journal.for_account(account_id):
            if transaction.booked > on:
                continue
            if transaction.currency == account.currency:
                amount += transaction.amount
            else:
                # A foreign-currency line on the statement: count it at that day's rate.
                home = to_home(transaction.amount, transaction.currency, rates, transaction.booked)
                amount += home if account.currency == rates.home else Decimal("0")
        own = Money(amount, account.currency)
        home_value = to_home(amount, account.currency, rates, on)
        found.append(Balance(account.id, account.name, own, Money(home_value, rates.home)))
    return found


def net_worth(rows: list[Balance], currency: str) -> Money:
    result = Money(Decimal("0"), currency)
    for row in rows:
        result = result + row.home
    return result
