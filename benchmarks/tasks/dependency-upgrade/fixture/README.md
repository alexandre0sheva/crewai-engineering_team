# invoicer

Totals for simple invoices (Python 3.11+). Its one dependency, `moneyfmt`, is vendored in
`vendor/` because the build machines have no network access; `requirements.txt` records the
version. New releases are unpacked into `vendor-updates/` for review before they are adopted.

```bash
python -m invoicer "Design=1,200.00" "Hosting=49.99"
python -m unittest discover -s tests
```
