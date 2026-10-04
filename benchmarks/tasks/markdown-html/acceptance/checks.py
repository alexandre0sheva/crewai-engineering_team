import re
import sys

from engineering_team.bench.checklib import expect, main, python


def convert(ws, text):
    sys.path.insert(0, str(ws))
    from md2html import convert as convert_function

    html = convert_function(text)
    expect(isinstance(html, str), "convert must return a string")
    return re.sub(r">\s+<", "><", html).strip()


def same(ws, text, expected):
    got = convert(ws, text)
    expect(got == expected, f"for {text!r} expected {expected!r}, got {got!r}")


def check_headings_and_paragraphs(ws):
    same(ws, "# Title", "<h1>Title</h1>")
    same(ws, "###### Deep", "<h6>Deep</h6>")
    same(
        ws,
        "## Two\n\nfirst line\nsecond line\n\nnext paragraph",
        "<h2>Two</h2><p>first line second line</p><p>next paragraph</p>",
    )
    same(ws, "#nospace", "<p>#nospace</p>")
    same(ws, "", "")


def check_inline_formatting(ws):
    same(
        ws,
        "a **bold** and *it* and _it2_ done",
        "<p>a <strong>bold</strong> and <em>it</em> and <em>it2</em> done</p>",
    )
    same(ws, "use `x = 1` here", "<p>use <code>x = 1</code> here</p>")
    same(
        ws,
        "see [the docs](https://example.com/a) now",
        '<p>see <a href="https://example.com/a">the docs</a> now</p>',
    )
    same(
        ws,
        "`**not bold**` but **bold**",
        "<p><code>**not bold**</code> but <strong>bold</strong></p>",
    )
    same(ws, "snake_case_name stays", "<p>snake_case_name stays</p>")


def check_lists(ws):
    same(ws, "- one\n- two\n* three", "<ul><li>one</li><li>two</li><li>three</li></ul>")
    same(ws, "1. first\n2. second", "<ol><li>first</li><li>second</li></ol>")
    same(ws, "- **a**\n- b\n\ntext", "<ul><li><strong>a</strong></li><li>b</li></ul><p>text</p>")
    same(ws, "intro\n\n- x\n\n1. y", "<p>intro</p><ul><li>x</li></ul><ol><li>y</li></ol>")


def check_code_blocks(ws):
    same(ws, "```\nprint('a < b')\n**x**\n```", "<pre><code>print('a &lt; b')\n**x**</code></pre>")
    same(
        ws,
        "before\n\n```\nx\n\ny\n```\n\nafter",
        "<p>before</p><pre><code>x\n\ny</code></pre><p>after</p>",
    )


def check_quotes_and_rules(ws):
    same(ws, "> quoted *text*\n> more", "<blockquote><p>quoted <em>text</em> more</p></blockquote>")
    same(ws, "above\n\n---\n\nbelow", "<p>above</p><hr><p>below</p>")


def check_escaping(ws):
    same(ws, "a < b & c > d", "<p>a &lt; b &amp; c &gt; d</p>")
    same(ws, "<script>alert(1)</script>", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    same(ws, "`<b>`", "<p><code>&lt;b&gt;</code></p>")
    same(ws, "- 1 < 2", "<ul><li>1 &lt; 2</li></ul>")


def check_cli(ws):
    (ws / "in.md").write_text("# Hi\n\nsome *text*\n")
    expected = "<h1>Hi</h1><p>some <em>text</em></p>"
    from_file = python(ws, "-m", "md2html", "in.md")
    expect(from_file.returncode == 0, f"exit {from_file.returncode}: {from_file.stderr[-200:]}")
    expect(
        re.sub(r">\s+<", "><", from_file.stdout).strip() == expected,
        f"file output was {from_file.stdout!r}",
    )
    from_stdin = python(ws, "-m", "md2html", input="# Hi\n\nsome *text*\n")
    expect(
        re.sub(r">\s+<", "><", from_stdin.stdout).strip() == expected,
        f"stdin output was {from_stdin.stdout!r}",
    )
    expect(
        python(ws, "-m", "md2html", "missing.md").returncode == 2, "an unreadable file must exit 2"
    )


if __name__ == "__main__":
    main()
