# `report income` crashes for March

Since I imported my March statement from Rabobank, `python -m ledgerly report income 2026-03`
crashes with a `TypeError` in the currency conversion (the traceback is attached). All my money
is in euros, so I don't see why it converts anything. February's report, imported the same way a
month earlier, still works. The import itself said nothing was wrong:

```text
$ python -m ledgerly import march.csv --format rabo --account checking
march.csv: 3 read, 3 added, 0 already there
```

The statement (shortened) looks like this:

```csv
Datum,Bedrag,Munt,Naam tegenpartij,Tegenrekening IBAN/BBAN,Omschrijving-1
2026-03-01,,EUR,,,Saldo begin
2026-03-02,"-1.020,00",EUR,Woonstichting,,Huur maart
2026-03-03,"-64,35",EUR,Albert Heijn 1403,,Boodschappen
2026-03-25,"+2.750,00",EUR,Acme BV,,Salaris maart
```

Please find the cause and fix it, so that importing this statement and reporting March gives the
right totals (my salary must be in there). As the README promises, an import must never drop or
invent money: if ledgerly cannot read a statement line, the import should stop with an error that
names the file and line, and add nothing. Everything that works today must keep working the same
way. Add a regression test to the suite (`python -m unittest discover -s tests`).
