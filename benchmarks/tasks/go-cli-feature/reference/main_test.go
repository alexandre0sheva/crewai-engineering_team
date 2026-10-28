package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestRun(t *testing.T) {
	const text = "a an the The cat cat catalogue"
	tests := []struct {
		name     string
		args     []string
		wantOut  string
		wantErr  string
		wantCode int
	}{
		{"default text", []string{"-top", "2"}, "2\tcat\n2\tthe\n", "", 0},
		{"min length", []string{"-min-length", "4"}, "1\tcatalogue\n", "", 0},
		{"json", []string{"-format", "json", "-top", "1"}, `[{"word":"cat","count":2}]` + "\n", "", 0},
		{"json with nothing left", []string{"-format", "json", "-min-length", "20"}, "[]\n", "", 0},
		{"bad format", []string{"-format", "xml"}, "", "wordstat: -format must be text or json\n", 2},
		{"bad min length", []string{"-min-length", "0"}, "", "wordstat: -min-length must be 1 or more\n", 2},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var out, errOut bytes.Buffer
			code := run(tt.args, strings.NewReader(text), &out, &errOut)
			if code != tt.wantCode || out.String() != tt.wantOut || errOut.String() != tt.wantErr {
				t.Errorf("run(%q) = %d, %q, %q; want %d, %q, %q", tt.args, code, out.String(),
					errOut.String(), tt.wantCode, tt.wantOut, tt.wantErr)
			}
		})
	}
}
