# wordstat

Counts the words in text files and prints the most frequent ones.

```bash
go run . -top 5 notes.txt more.txt   # the five most frequent words over both files
go run . < notes.txt                 # read standard input; default -top 10
go test ./...
```

A word is a run of letters, digits and apostrophes, compared in lower case. Output is one
`count<TAB>word` line per word, most frequent first, ties in alphabetical order. Errors go to
standard error as `wordstat: <message>` with exit status 2 (bad flags) or 1 (unreadable file).
