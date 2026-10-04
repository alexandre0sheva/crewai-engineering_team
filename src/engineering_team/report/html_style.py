"""The report's inline CSS and JavaScript (no external files, no CDN)."""

from __future__ import annotations

# Content-Security-Policy: nothing is loaded from anywhere, images are only the embedded ones, and
# the one inline script is the theme toggle. If escaping ever failed, this still stops a payload
# from reaching the network.
CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--text:#1c2330;--muted:#5d6879;--line:#d9dee6;--accent:#2457c5;
--good:#17794a;--good-bg:#e4f5ec;--warn:#8a5a00;--warn-bg:#fff2d6;--bad:#b3261e;--bad-bg:#fde8e6;
--info:#41506a;--info-bg:#e9edf4;--add:#e6f6ea;--del:#fdecea;--code:#eef1f5;--bar:#7a98d8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#12161d;--panel:#1a2029;
--text:#e3e8f0;--muted:#97a3b6;--line:#2b3442;--accent:#8fb0ff;--good:#6fd39e;--good-bg:#173626;
--warn:#f0c46a;--warn-bg:#3b2e10;--bad:#ff8f86;--bad-bg:#40191a;--info:#aebbd1;--info-bg:#252d3a;
--add:#173626;--del:#40191a;--code:#222a36;--bar:#4f6fb3}}
:root[data-theme="dark"]{--bg:#12161d;--panel:#1a2029;--text:#e3e8f0;--muted:#97a3b6;
--line:#2b3442;--accent:#8fb0ff;--good:#6fd39e;--good-bg:#173626;--warn:#f0c46a;--warn-bg:#3b2e10;
--bad:#ff8f86;--bad-bg:#40191a;--info:#aebbd1;--info-bg:#252d3a;--add:#173626;--del:#40191a;
--code:#222a36;--bar:#4f6fb3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
header.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{font-size:1.4rem;margin:.2em 0}h2{font-size:1.15rem;margin:0 0 .6em}
h3{font-size:1rem;margin:1em 0 .4em}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;
margin:16px 0}
nav.toc{display:flex;gap:6px 14px;flex-wrap:wrap;margin:8px 0;font-size:.9rem}
a{color:var(--accent)}
button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);
border-radius:6px;padding:4px 10px;cursor:pointer}
.banner{border-radius:8px;padding:14px 16px;border:1px solid var(--line)}
.banner h2{margin:0;font-size:1.25rem}
.banner ul{margin:.5em 0 0 1.2em;padding:0}
.good{background:var(--good-bg);color:var(--good)}.warn{background:var(--warn-bg);color:var(--warn)}
.bad{background:var(--bad-bg);color:var(--bad)}.info{background:var(--info-bg);color:var(--info)}
.banner li{color:var(--text)}
.badge{display:inline-block;border-radius:10px;padding:0 8px;font-size:.8rem;font-weight:600}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:0}
.facts div{background:var(--bg);border-radius:6px;padding:8px 10px}
.facts dt{color:var(--muted);font-size:.8rem}.facts dd{margin:0;word-break:break-word}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.82rem}
code{background:var(--code);padding:0 4px;border-radius:4px}
pre{background:var(--code);padding:10px;border-radius:6px;overflow-x:auto;margin:.4em 0;
white-space:pre-wrap;word-break:break-word}
pre.diff{white-space:pre;word-break:normal}
pre.diff span{display:block}.diff .add{background:var(--add)}.diff .del{background:var(--del)}
.diff .hunk{color:var(--muted)}
details{margin:.3em 0}summary{cursor:pointer}
.muted{color:var(--muted)}
.tl{position:relative;font-size:.8rem}
.tl-row{display:grid;grid-template-columns:200px 1fr;gap:8px;align-items:center;margin:3px 0}
.tl-lane{padding-left:14px;color:var(--muted)}
.tl-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl-track{position:relative;height:16px;background:var(--bg);border-radius:4px}
.tl-bar{position:absolute;top:0;bottom:0;border-radius:4px;background:var(--bar);min-width:3px}
.tl-bar.succeeded{background:var(--good)}.tl-bar.failed{background:var(--bad)}
.tl-bar.cancelled,.tl-bar.interrupted,.tl-bar.skipped{background:var(--warn)}
.tl-axis{display:flex;justify-content:space-between;color:var(--muted);margin-left:208px}
.meter{height:8px;background:var(--bg);border-radius:4px;min-width:90px}
.meter i{display:block;height:100%;border-radius:4px;background:var(--bar)}
.meter i.warn{background:var(--warn)}.meter i.bad{background:var(--bad)}
.columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.col{background:var(--bg);border-radius:6px;padding:8px}.col h3{margin:0 0 6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;
margin:6px 0;font-size:.85rem}
figure{margin:8px 0}figure img{max-width:100%;border:1px solid var(--line);border-radius:6px}
@media (max-width:600px){main{padding:8px}section{padding:12px}
.tl-row{grid-template-columns:90px 1fr}.tl-axis{margin-left:98px}}
@media print{body{background:#fff}details{display:block}button,nav.toc{display:none}}
"""

# Cycles auto → light → dark. No storage: the page works the same without it.
JS = """
(function(){var b=document.getElementById('theme');if(!b)return;var r=document.documentElement;
var modes=['auto','light','dark'],i=0;
b.addEventListener('click',function(){i=(i+1)%3;var m=modes[i];
if(m==='auto'){r.removeAttribute('data-theme')}else{r.setAttribute('data-theme',m)}
b.textContent='Theme: '+m});})();
"""
