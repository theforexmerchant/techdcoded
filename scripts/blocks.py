"""Visual building blocks for articles.

Writers (the AI) use simple fenced blocks inside the Markdown body, e.g.

:::steps How a UPI payment travels
1. You scan | The app reads the UPI ID from the QR code
2. NPCI routes | The central switch finds the receiver's bank
:::

Supported: steps, stats, tip, didyouknow, warning, myth, compare, timeline, quote, keyterm.
They are turned into designed HTML components (diagrams, stat cards, callouts...) at build time.
"""
import html, re
import markdown

e = lambda s: html.escape(str(s).strip(), quote=True)
BLOCK_RE = re.compile(r"^:::[ \t]*([a-zA-Z]+)[ \t]*(.*?)\n(.*?)\n:::[ \t]*$", re.M | re.S)

ICONS = {  # small inline SVG icons (stroke = currentColor)
    "tip": '<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/>',
    "didyouknow": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    "warning": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
    "myth": '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="10"/>',
    "quote": '<path d="M7 17h3l2-4V7H6v6h3zm8 0h3l2-4V7h-6v6h3z"/>',
    "keyterm": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/>',
}
TITLES = {"tip": "Pro tip", "didyouknow": "Did you know?", "warning": "Watch out", "keyterm": "Key term"}


def svg(name):
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>'


def md_inline(text):
    out = markdown.markdown(text.strip())
    return out


def rows(body, n=2):
    out = []
    for line in body.splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        out.append((parts + [""] * n)[:n] if n > 1 else parts)
    return out


def render(kind, title, body):
    kind = kind.lower()
    if kind == "steps":
        items = rows(body)
        li = "".join(f'<li><span class="sn">{i}</span><div><b>{e(a)}</b>{f"<p>{e(b)}</p>" if b else ""}</div></li>' for i, (a, b) in enumerate(items, 1))
        return f'<figure class="vb steps">{f"<figcaption>{e(title)}</figcaption>" if title else ""}<ol>{li}</ol></figure>'
    if kind == "stats":
        items = rows(body)
        cells = "".join(f'<div class="st"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in items)
        return f'<figure class="vb stats">{f"<figcaption>{e(title)}</figcaption>" if title else ""}<div class="sg">{cells}</div></figure>'
    if kind == "timeline":
        items = rows(body)
        li = "".join(f'<li><time>{e(a)}</time><p>{e(b)}</p></li>' for a, b in items)
        return f'<figure class="vb timeline">{f"<figcaption>{e(title)}</figcaption>" if title else ""}<ol>{li}</ol></figure>'
    if kind == "compare":
        left, _, right = (title or "Option A vs Option B").partition(" vs ")
        items = rows(body)
        l = "".join(f"<li>{e(a)}</li>" for a, _ in items if a)
        r = "".join(f"<li>{e(b)}</li>" for _, b in items if b)
        return f'<figure class="vb compare"><div class="cl"><h4>{e(left)}</h4><ul>{l}</ul></div><div class="vs">VS</div><div class="cr"><h4>{e(right or "Option B")}</h4><ul>{r}</ul></div></figure>'
    if kind == "myth":
        m = re.search(r"myth\s*:\s*(.*?)(?:\n|$)", body, re.I | re.S)
        f = re.search(r"fact\s*:\s*(.*)", body, re.I | re.S)
        myth, fact = (m.group(1) if m else body), (f.group(1) if f else "")
        return f'<aside class="vb myth"><div class="mm"><span>✕ Myth</span><p>{e(myth)}</p></div><div class="mf"><span>✓ Fact</span><p>{e(fact)}</p></div></aside>'
    if kind == "quote":
        return f'<blockquote class="vb pull">{svg("quote")}<p>{e(body)}</p>{f"<cite>{e(title)}</cite>" if title else ""}</blockquote>'
    if kind in ("tip", "didyouknow", "warning", "keyterm"):
        head = title or TITLES[kind]
        return f'<aside class="vb call {kind}"><div class="ci">{svg(kind)}</div><div><b>{e(head)}</b>{md_inline(body)}</div></aside>'
    return None


def image_html(img):
    cap = e(img.get("caption", ""))
    credit = ""
    if img.get("credit"):
        c = e(img["credit"])
        if img.get("credit_url"):
            c = f'<a href="{e(img["credit_url"])}" target="_blank" rel="nofollow noopener">{c}</a>'
        credit = f' <span class="cr">{c}</span>'
    base = f"/assets/articles/{img['file']}"
    return (f'<figure class="vb photo"><picture><source srcset="{base}.webp" type="image/webp"><img src="{base}.jpg" alt="{e(img.get("alt", cap))}" '
            f'width="{img.get("w", 1200)}" height="{img.get("h", 675)}" loading="lazy"></picture>'
            f'{f"<figcaption>{cap}{credit}</figcaption>" if cap or credit else ""}</figure>')


def expand(md_text, images=None):
    """Replace ::: blocks with placeholders; return (markdown, {placeholder: html}).
    Images are placed right after the '## heading' they belong to."""
    store = {}

    def sub(m):
        out = render(m.group(1), m.group(2), m.group(3))
        if out is None:
            return m.group(0)
        key = f"VBLOCK{len(store)}X"
        store[key] = out
        return f"\n\n{key}\n\n"

    md_text = BLOCK_RE.sub(sub, md_text)
    fallback = 0
    for img in images or []:
        key = f"VBLOCK{len(store)}X"
        store[key] = image_html(img)
        h = img.get("after_heading", "").strip().lower()
        placed = False
        if h:
            lines = md_text.split("\n")
            for i, ln in enumerate(lines):
                if ln.startswith("## ") and ln[3:].strip().lower().startswith(h[:40]):
                    # after the heading's first paragraph
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    while j < len(lines) and lines[j].strip():
                        j += 1
                    lines.insert(j, f"\n{key}\n")
                    md_text = "\n".join(lines); placed = True
                    break
        if not placed:  # fallback: spread the leftovers evenly through the article
            parts = re.split(r"(?m)^(?=## )", md_text)
            total = max(1, len(images or []))
            step = max(1, (len(parts) - 1) // total)
            idx = min(len(parts) - 1, 1 + fallback * step)
            parts.insert(idx, f"{key}\n\n")
            md_text = "".join(parts)
            fallback += 1
    return md_text, store


def restore(html_text, store):
    for k, v in store.items():
        html_text = html_text.replace(f"<p>{k}</p>", v).replace(k, v)
    return html_text
