#!/usr/bin/env python3
"""Convert a Markdown file to a standalone, styled HTML page.

- GFM tables + fenced code (markdown 'extra'), heading anchors + a Contents nav (toc).
- Rewrites links to a provided set of sibling .md files -> .html so the converted set is
  browsable together; links to non-converted .md files are left as-is.
Usage: md2html.py IN.md OUT.html [--title T] [--rewrite a.md,b.md,...] [--subtitle S]

Dependencies: pip install markdown pymdown-extensions  (run with the project venv python).
Used to (re)generate the architecture-review + components HTML in docs/.
"""
import argparse
import datetime
import html
import re

import markdown

CSS = """
:root { --fg:#1f2328; --muted:#656d76; --link:#0969da; --border:#d0d7de; --bg:#fff;
        --code-bg:#f6f8fa; --accent:#0969da; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  font-size:16px; line-height:1.6; }
.wrap { max-width:920px; margin:0 auto; padding:32px 24px 80px; }
header.doc { border-bottom:1px solid var(--border); margin-bottom:24px; padding-bottom:16px; }
header.doc h1 { margin:0 0 4px; font-size:1.9rem; }
header.doc .sub { color:var(--muted); font-size:.9rem; }
header.doc .sub code { background:var(--code-bg); padding:1px 5px; border-radius:4px; }
nav.toc { background:var(--code-bg); border:1px solid var(--border); border-radius:8px;
  padding:12px 16px; margin:0 0 28px; font-size:.92rem; }
nav.toc .toc-title { font-weight:600; margin-bottom:6px; }
nav.toc ul { margin:4px 0; padding-left:20px; }
nav.toc a { color:var(--link); text-decoration:none; }
nav.toc a:hover { text-decoration:underline; }
h1,h2,h3,h4 { line-height:1.25; margin-top:1.6em; margin-bottom:.5em; font-weight:600; }
h2 { border-bottom:1px solid var(--border); padding-bottom:.3em; }
h1 { font-size:1.7rem; } h2 { font-size:1.4rem; } h3 { font-size:1.15rem; } h4 { font-size:1rem; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
p, li { overflow-wrap:break-word; }
code { background:var(--code-bg); padding:.15em .4em; border-radius:5px;
  font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace; font-size:.88em; }
pre { background:var(--code-bg); border:1px solid var(--border); border-radius:8px;
  padding:14px 16px; overflow-x:auto; line-height:1.45; }
pre code { background:none; padding:0; font-size:.82rem; white-space:pre; }
blockquote { margin:1em 0; padding:.4em 1em; color:var(--muted);
  border-left:4px solid var(--border); background:#f6f8fa55; }
blockquote p { margin:.4em 0; }
table { border-collapse:collapse; width:100%; margin:1.2em 0; font-size:.93rem; display:block; overflow-x:auto; }
th, td { border:1px solid var(--border); padding:7px 12px; text-align:left; vertical-align:top; }
th { background:var(--code-bg); font-weight:600; }
tr:nth-child(even) td { background:#f6f8fa55; }
hr { border:0; border-top:1px solid var(--border); margin:2em 0; }
.anchor { visibility:hidden; padding-right:6px; text-decoration:none; color:var(--muted); }
h1:hover .anchor, h2:hover .anchor, h3:hover .anchor, h4:hover .anchor { visibility:visible; }
footer.doc { margin-top:48px; padding-top:16px; border-top:1px solid var(--border);
  color:var(--muted); font-size:.82rem; }
@media (max-width:640px){ .wrap{padding:20px 14px 60px;} body{font-size:15px;} }
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header class="doc">
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
</header>
{toc}
{body}
<footer class="doc">Generated from <code>{src}</code> on {date} — RuntimeManager architecture docs.</footer>
</div>
</body>
</html>
"""


def rewrite_links(html_text: str, rewrite: list[str]) -> str:
    for name in rewrite:
        if not name.endswith(".md"):
            continue
        stem = re.escape(name[:-3])
        html_text = re.sub(rf'href="(\./)?{stem}\.md(#[^"]*)?"',
                           lambda m: f'href="{m.group(1) or ""}{name[:-3]}.html{m.group(2) or ""}"',
                           html_text)
    return html_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--rewrite", default="")
    args = ap.parse_args()

    text = open(args.inp).read()
    title = args.title
    if not title:
        m = re.search(r"^#\s+(.+)$", text, re.M)
        title = m.group(1).strip() if m else args.inp

    md = markdown.Markdown(extensions=["extra", "sane_lists", "toc", "admonition", "attr_list"],
                           extension_configs={"toc": {"permalink": "¶", "permalink_class": "anchor"}})
    body = md.convert(text)
    toc = ""
    if getattr(md, "toc_tokens", None):
        toc = f'<nav class="toc"><div class="toc-title">Contents</div>{md.toc}</nav>'

    rewrite = [s.strip() for s in args.rewrite.split(",") if s.strip()]
    body = rewrite_links(body, rewrite)
    toc = rewrite_links(toc, rewrite)

    page = TEMPLATE.format(
        title=html.escape(title), css=CSS, subtitle=args.subtitle or "",
        toc=toc, body=body, src=html.escape(args.inp.split("/")[-1]),
        date=datetime.date.today().isoformat())
    open(args.out, "w").write(page)
    print(f"wrote {args.out}  ({len(page)} bytes, title={title!r})")


if __name__ == "__main__":
    main()
