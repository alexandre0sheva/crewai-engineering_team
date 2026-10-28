// Command wordstat prints the most frequent words in text files.
package main

import (
	"flag"
	"fmt"
	"io"
	"os"

	"example.com/wordstat/internal/count"
)

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("wordstat", flag.ContinueOnError)
	flags.SetOutput(stderr)
	top := flags.Int("top", 10, "how many words to print (0: all)")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if *top < 0 {
		fmt.Fprintln(stderr, "wordstat: -top must be 0 or more")
		return 2
	}
	counter := count.New()
	if flags.NArg() == 0 {
		if err := counter.Add(stdin); err != nil {
			fmt.Fprintf(stderr, "wordstat: %v\n", err)
			return 1
		}
	}
	for _, name := range flags.Args() {
		file, err := os.Open(name)
		if err != nil {
			fmt.Fprintf(stderr, "wordstat: cannot read %s\n", name)
			return 1
		}
		err = counter.Add(file)
		file.Close()
		if err != nil {
			fmt.Fprintf(stderr, "wordstat: %s: %v\n", name, err)
			return 1
		}
	}
	for _, entry := range counter.Top(*top) {
		fmt.Fprintf(stdout, "%d\t%s\n", entry.Count, entry.Word)
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}
