package count

import (
	"reflect"
	"strings"
	"testing"
)

func TestWords(t *testing.T) {
	tests := []struct {
		name string
		text string
		want []string
	}{
		{"simple", "The cat sat", []string{"the", "cat", "sat"}},
		{"punctuation", "Hello, world! hello.", []string{"hello", "world", "hello"}},
		{"apostrophes", "don't 'quote'", []string{"don't", "quote"}},
		{"empty", "  ... ", []string{}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := Words(tt.text); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("Words(%q) = %q, want %q", tt.text, got, tt.want)
			}
		})
	}
}

func TestTopOrdersByCountThenWord(t *testing.T) {
	c := New()
	if err := c.Add(strings.NewReader("b a c b a b")); err != nil {
		t.Fatal(err)
	}
	want := []Entry{{"b", 3}, {"a", 2}}
	if got := c.Top(2); !reflect.DeepEqual(got, want) {
		t.Errorf("Top(2) = %v, want %v", got, want)
	}
}
