# Upgrade moneyfmt to 2.0.0

`moneyfmt` 2.0.0 has been unpacked into `vendor-updates/moneyfmt-2.0.0/` (its `CHANGELOG.md` lists
the breaking changes). Please upgrade the project to it:

- the vendored copy in `vendor/moneyfmt/` becomes 2.0.0 (the released files, unmodified), and
  `requirements.txt` pins `moneyfmt==2.0.0`;
- adapt the invoicer code to the new API, so nothing uses what 2.0.0 removed or changed;
- `python -m invoicer ...` prints the same invoices and the same error messages, with the same exit
  status, as before. The one acceptable difference: amounts are now exact decimals, so a float
  rounding error that used to move a total by a cent may no longer happen.

The existing tests must keep passing (`python -m unittest discover -s tests`); add a test that would
have caught a missed API change.
