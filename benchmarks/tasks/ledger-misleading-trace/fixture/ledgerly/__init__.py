"""ledgerly: a small personal ledger.

Import bank statements (several CSV formats), categorise the transactions with rules, convert
foreign currencies to the home currency, and report income, expenses and balances per month.

Packages:

- ``core``: money, accounts, transactions, the journal, periods and errors
- ``io``: reading statements (``csv_import``, ``bank_formats``) and the JSON ledger file
- ``fx``: exchange rates and conversion to the home currency
- ``rules``: categorisation rules and their matchers
- ``reports``: income, balances and their text rendering
- ``util``: small text and date helpers
"""

__version__ = "3.2.1"
