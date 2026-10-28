# ledgerly

A small personal ledger (Python 3.11+, standard library only). Import bank statements, have them
categorised by rules, and report income, expenses and balances per month.

```bash
python -m ledgerly account add checking "Joint account" --iban "NL91 ABNA 0417 1643 00"
python -m ledgerly import march.csv --format rabo --account checking
python -m ledgerly report income 2026-03 --compare
python -m ledgerly report balance
python -m unittest discover -s tests
```

Statement formats: `generic`, `ing` and `rabo` (see `ledgerly/io/bank_formats.py`). Foreign
currencies are converted with the rates in `rates.csv` (`date,currency,rate`). The ledger is one
JSON file (`ledger.json`).

Importing never drops or invents money: a statement line that cannot be read stops the import with
an error naming the file and line, and nothing from that file is added.
