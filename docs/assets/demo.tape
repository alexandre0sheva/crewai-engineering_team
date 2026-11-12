# A terminal recording of `do` fixing a bug in a small real repository (examples/bugfix/repo).
#   vhs docs/assets/demo.tape        # renders docs/assets/demo.gif; needs vhs, ttyd and ffmpeg
# It makes a live model run (the cheapest passing preset, a few cents): put your key in the
# environment first, and set ENGINEERING_BUDGET_MAX_COST_USD if you want a lower cap.
Output docs/assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 720
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set WaitTimeout 15m

# Setup, off camera: a throwaway copy of the example repository.
Hide
Type "rm -rf /tmp/shop-demo && cp -R examples/bugfix/repo /tmp/shop-demo" Enter
Type "git -C /tmp/shop-demo init -q -b main && git -C /tmp/shop-demo add -A && git -C /tmp/shop-demo commit -qm Initial" Enter
Type "clear" Enter
Show

Type "# A crash on an empty cart, reported with its stack trace" Enter
Sleep 1s
Type `engineering-team do "Fix the ZeroDivisionError in 'shop report' on an empty cart. Expected: Items: 0, total: 0.00, average: n/a" --repo /tmp/shop-demo --trace-file examples/bugfix/trace.txt --yes` Enter

# The run ends with the verdict line and the report path.
Wait+Screen@15m /[Vv]erdict/
Sleep 8s

Type "git -C /tmp/shop-demo log --oneline -3" Enter
Sleep 4s
