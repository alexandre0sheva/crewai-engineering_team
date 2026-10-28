# Put the shared money rules in one module

`billing/quote.py`, `billing/invoice.py` and `billing/receipt.py` each carry their own copy of the
same money rules: rounding to the cent (half up), pricing the item lines and checking quantities,
the discount (0 to 50 percent), and the tax rates and tax arithmetic. Last month a rate change was
made in one copy and missed in another.

Please move those rules into a new module, `billing/pricing.py`, and make the three modules use it,
so that each rule (the rounding, the line pricing, the discount, the tax rates and each tax
calculation) is written exactly once. This is a refactor: the behaviour must not change at all,
including every amount, every error message, and which error comes first when an input has more
than one problem. `build_quote`, `build_invoice`, `build_receipt`, their result classes and
`PAYMENT_DAYS` stay where they are, with the same signatures. Leave the existing tests as they are;
they must keep passing (`python -m unittest discover -s tests`).
