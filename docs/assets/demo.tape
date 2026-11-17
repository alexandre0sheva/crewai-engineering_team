# A terminal recording of `do` fixing a bug in a small real repository (examples/bugfix/repo).
#   vhs docs/assets/demo.tape        # renders docs/assets/demo.gif; needs vhs, ttyd and ffmpeg
# It makes a live model run on the `openai` preset (the cheapest per success in BENCHMARKS.md, a
# few cents): put OPENAI_API_KEY in the environment first, and set ENGINEERING_BUDGET_MAX_COST_USD
# if you want a lower cap. The committed GIF was rendered with the shell's ENGINEERING_* settings
# blanked, so a model chosen there does not replace the preset's.
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
Type `{ cat examples/bugfix/request.md; printf '\n~~~text\n'; cat examples/bugfix/trace.txt; printf '~~~\n'; } > /tmp/shop-bug.md` Enter
Type "clear" Enter
Show

Type "# A crash on an empty cart, reported with its stack trace" Enter
Sleep 1s
Type "head -5 /tmp/shop-bug.md" Enter
Sleep 3s
Type "engineering-team do --request-file /tmp/shop-bug.md --repo /tmp/shop-demo --provider openai --yes" Enter

# The run takes about 1.5 minutes and ends with a summary panel (verdict in its title, the checks,
# the report path). `Wait` cannot watch it: the live board redraws in place, which vhs's screen
# matching does not follow, so the tape sleeps instead.
Sleep 150s

Type "git -C /tmp/shop-demo log --oneline -3" Enter
Sleep 4s
