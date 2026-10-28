// Command wordstat prints the most frequent words in text files.
package main

import (
	"encoding/json"
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
	minLength := flags.Int("min-length", 1, "skip words shorter than this")
	format := flags.String("format", "text", "output format: text or json")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if *top < 0 {
		fmt.Fprintln(stderr, "wordstat: -top must be 0 or more")
		return 2
	}
	if *minLength < 1 {
		fmt.Fprintln(stderr, "wordstat: -min-length must be 1 or more")
		return 2
	}
	if *format != "text" && *format != "json" {
		fmt.Fprintln(stderr, "wordstat: -format must be text or json")
		return 2
	}
	counter := count.New()
	counter.SetMinLength(*minLength)
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
	entries := counter.Top(*top)
	if *format == "json" {
		type item struct {
			Word  string `json:"word"`
			Count int    `json:"count"`
		}
		items := make([]item, 0, len(entries))
		for _, entry := range entries {
			items = append(items, item{entry.Word, entry.Count})
		}
		data, err := json.Marshal(items)
		if err != nil {
			fmt.Fprintf(stderr, "wordstat: %v\n", err)
			return 1
		}
		fmt.Fprintln(stdout, string(data))
		return 0
	}
	for _, entry := range entries {
		fmt.Fprintf(stdout, "%d\t%s\n", entry.Count, entry.Word)
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}
