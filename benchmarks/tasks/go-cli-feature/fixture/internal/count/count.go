// Package count counts words and ranks them.
package count

import (
	"bufio"
	"io"
	"sort"
	"strings"
	"unicode"
)

// Entry is one word and how often it occurs.
type Entry struct {
	Word  string
	Count int
}

// Counter accumulates word counts over several inputs.
type Counter struct {
	counts map[string]int
}

// New returns an empty Counter.
func New() *Counter {
	return &Counter{counts: map[string]int{}}
}

func isWordRune(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsDigit(r) || r == '\''
}

// Words splits text into lower-case words.
func Words(text string) []string {
	fields := strings.FieldsFunc(text, func(r rune) bool { return !isWordRune(r) })
	words := fields[:0]
	for _, field := range fields {
		word := strings.ToLower(strings.Trim(field, "'"))
		if word != "" {
			words = append(words, word)
		}
	}
	return words
}

// Add counts the words read from r.
func (c *Counter) Add(r io.Reader) error {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	for scanner.Scan() {
		for _, word := range Words(scanner.Text()) {
			c.counts[word]++
		}
	}
	return scanner.Err()
}

// Top returns the n most frequent words (all of them if n <= 0), most frequent first and ties in
// alphabetical order.
func (c *Counter) Top(n int) []Entry {
	entries := make([]Entry, 0, len(c.counts))
	for word, count := range c.counts {
		entries = append(entries, Entry{Word: word, Count: count})
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].Count != entries[j].Count {
			return entries[i].Count > entries[j].Count
		}
		return entries[i].Word < entries[j].Word
	})
	if n > 0 && n < len(entries) {
		entries = entries[:n]
	}
	return entries
}
