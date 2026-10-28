# billing

Quotes, invoices and receipts for a small workshop (Python 3.11+, standard library only).

```bash
python -m billing quote DE --discount 10 "Brake pads:34.995:2" "Labour:60:1"
python -m billing receipt NL --paid 200 "Brake pads:34.995:2" "Labour:60:1"
python -m unittest discover -s tests
```

Amounts are rounded to the cent, half up. Tax rates: DE 19 %, FR 20 %, NL 21 %. Discounts are
whole percents from 0 to 50.
