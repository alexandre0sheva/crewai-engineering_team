"""Block structure."""

import re

from md2html.inline import escape, render

HEADING = re.compile(r"(#{1,6}) +(.+?)\s*$")
BULLET = re.compile(r"[-*] +(.*)")
NUMBER = re.compile(r"\d+\. +(.*)")
RULE = re.compile(r"-{3,}\s*$")


def starts_block(line: str) -> bool:
    return bool(
        not line.strip()
        or line.startswith(("```", ">"))
        or HEADING.match(line)
        or RULE.match(line)
        or BULLET.match(line)
        or NUMBER.match(line)
    )


def convert(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{escape(chr(10).join(code))}</code></pre>")
        elif match := HEADING.match(line):
            level = len(match.group(1))
            out.append(f"<h{level}>{render(match.group(2))}</h{level}>")
            i += 1
        elif RULE.match(line):
            out.append("<hr>")
            i += 1
        elif line.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(re.sub(r"^> ?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{convert(chr(10).join(quoted))}</blockquote>")
        elif BULLET.match(line) or NUMBER.match(line):
            pattern, tag = (BULLET, "ul") if BULLET.match(line) else (NUMBER, "ol")
            items = []
            while i < len(lines) and (m := pattern.match(lines[i])):
                items.append(f"<li>{render(m.group(1))}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            paragraph = [line.strip()]
            i += 1
            while i < len(lines) and not starts_block(lines[i]):
                paragraph.append(lines[i].strip())
                i += 1
            out.append(f"<p>{render(' '.join(paragraph))}</p>")
    return "\n".join(out)
