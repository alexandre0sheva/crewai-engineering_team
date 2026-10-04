# Markdown to HTML converter

Build a small Markdown-to-HTML converter in Python 3.11 or newer, using only the standard library.

It is a package `md2html/` in the project root (not under `src/`) that offers:

- a function `md2html.convert(text: str) -> str` (import it as `from md2html import convert`);
- a command, `python -m md2html [FILE]`, that converts FILE (or stdin when no FILE is given) and
  writes the HTML to stdout. A file that cannot be read exits with status 2 and a message on stderr.

## What to support

Output is compared after removing whitespace between tags, so how you lay it out is up to you.

- Headings: `#` to `######` followed by a space become `<h1>` to `<h6>`.
- Paragraphs: consecutive non-blank lines form a `<p>`; the lines are joined with a single space.
- Inline: `**bold**` is `<strong>`, `*italic*` and `_italic_` are `<em>`, `` `code` `` is `<code>`, and
  `[text](url)` is `<a href="url">text</a>`. Nothing inside inline code is formatted.
- Unordered lists (lines starting with `- ` or `* `) become `<ul>` with one `<li>` per item;
  ordered lists (`1. `, `2. `, ...) become `<ol>`. Inline formatting works inside items.
- Fenced code blocks (a line of three backticks, up to the next such line) become
  `<pre><code>...</code></pre>` with the content exactly as written (escaped, nothing formatted).
- Block quotes: consecutive lines starting with `>` become `<blockquote>` around the converted
  content of those lines (with the `>` and one following space removed).
- A line consisting only of `---` is `<hr>`.
- `&`, `<` and `>` in the text, including inside code, are escaped as `&amp;`, `&lt;` and `&gt;`.

Blank input gives an empty string. Add tests and a short README.
