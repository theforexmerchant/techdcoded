"""Builds all article pages, the /articles/ index, featured images, sitemap.xml, feed.xml,
and the 'Latest articles' block on the home page from content/articles/*.json."""
import hashlib, html, json, re, textwrap
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xesc
import markdown
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter
import blocks
from common import ROOT, SITE, SOCIAL, load_articles, load_embeddings, cosine, now_ist

# version tag so browsers always fetch the newest stylesheet after a change
CSS_V = hashlib.md5((ROOT / "assets" / "site.css").read_bytes()).hexdigest()[:8]

OUT = ROOT / "articles"
IMG = ROOT / "assets" / "articles"
FONTS = ROOT / "scripts" / "fonts"
METRICOOL = '<script>function loadScript(a){var b=document.getElementsByTagName("head")[0],c=document.createElement("script");c.type="text/javascript",c.src="https://tracker.metricool.com/resources/be.js",c.onreadystatechange=a,c.onload=a,b.appendChild(c)}loadScript(function(){beTracker.t({hash:"5b8fca006781a420995665ad57682a95"})});</script>'
e = lambda s: html.escape(str(s), quote=True)


def fmt_date(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")


def read_time(wc):
    return max(3, round(wc / 220))


# ---------------- featured image ----------------
def font(name, size):
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default()


def make_image(a):
    IMG.mkdir(parents=True, exist_ok=True)
    jpg, webp = IMG / f"{a['slug']}.jpg", IMG / f"{a['slug']}.webp"
    if jpg.exists() and webp.exists():
        return
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), (5, 10, 30))
    d = ImageDraw.Draw(im)
    for y in range(H):  # vertical gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(8 + 6 * t), int(22 - 10 * t), int(70 - 35 * t)))
    glow = Image.new("RGB", (W, H), (0, 0, 0)); gd = ImageDraw.Draw(glow)
    gd.ellipse((780, -160, 1380, 440), fill=(20, 110, 255)); gd.ellipse((-200, 380, 400, 900), fill=(90, 50, 200))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    im = ImageChops.add(im, glow)
    d = ImageDraw.Draw(im)
    photo = IMG / f"{a['cover']}.jpg" if a.get("cover") and (IMG / f"{a['cover']}.jpg").exists() else \
        next((IMG / f"{x['file']}.jpg" for x in a.get("images", []) if (IMG / f"{x['file']}.jpg").exists()), None)
    if photo:  # use the article's first picture as the cover, with a brand-coloured shade for legible text
        ph = Image.open(photo).convert("RGB")
        sc = max(W / ph.width, H / ph.height)
        ph = ph.resize((round(ph.width * sc), round(ph.height * sc)), Image.LANCZOS)
        l, t = (ph.width - W) // 2, (ph.height - H) // 2
        ph = ph.crop((l, t, l + W, t + H))
        shade = Image.new("L", (W, H))
        sd = ImageDraw.Draw(shade)
        for x in range(W):
            sd.line([(x, 0), (x, H)], fill=int(235 - 150 * (x / W) ** 1.4))
        im = Image.composite(Image.new("RGB", (W, H), (5, 12, 40)), ph, shade)
        im = ImageChops.add(im, glow.point(lambda v: v // 3))
        d = ImageDraw.Draw(im)
    else:
        for x in range(0, W, 48):
            d.line([(x, 0), (x, H)], fill=(20, 40, 95))
        for y in range(0, H, 48):
            d.line([(0, y), (W, y)], fill=(20, 40, 95))
    # logo mark
    mark_p = ROOT / "assets" / "logo-mark.png"
    if mark_p.exists():
        m = Image.open(mark_p).convert("RGB").resize((150, 150))
        mask = Image.new("L", (150, 150), 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, 149, 149), 28, fill=255)
        im.paste(m, (W - 210, 60), mask)
    d = ImageDraw.Draw(im)
    # category pill
    cat = a["category"].upper()
    f_cat = font("Poppins-Medium.ttf", 24)
    tw = d.textlength(cat, font=f_cat)
    d.rounded_rectangle((70, 70, 70 + tw + 44, 118), 24, outline=(63, 208, 255), width=2, fill=(10, 40, 80))
    d.text((92, 76), cat, font=f_cat, fill=(63, 208, 255))
    # title
    title = a["title"]
    for size in (68, 62, 56, 50, 46):
        f_t = font("Poppins-Bold.ttf", size)
        lines = textwrap.wrap(title, width=int(900 / (size * 0.56)))
        if len(lines) <= 4:
            break
    y = 160 if len(lines) <= 3 else 145
    for ln in lines[:4]:
        d.text((70, y), ln, font=f_t, fill=(255, 255, 255))
        y += int(size * 1.22)
    # footer
    d.line([(70, H - 92), (W - 70, H - 92)], fill=(40, 90, 160), width=2)
    f_f = font("Poppins-Bold.ttf", 30)
    d.text((70, H - 72), "TECH", font=f_f, fill=(235, 240, 250))
    x = 70 + d.textlength("TECH", font=f_f)
    d.text((x, H - 72), "D", font=f_f, fill=(40, 150, 255))
    x += d.textlength("D", font=f_f)
    d.text((x, H - 72), "CODED", font=f_f, fill=(235, 240, 250))
    f_s = font("Poppins-Medium.ttf", 24)
    s = "techdcoded.com"
    d.text((W - 70 - d.textlength(s, font=f_s), H - 66), s, font=f_s, fill=(160, 185, 225))
    im.save(jpg, quality=86, optimize=True, progressive=True)
    im.save(webp, quality=80, method=6)


# ---------------- shared chrome ----------------
def head(title, desc, url, image, extra=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="canonical" href="{url}">
<link rel="icon" type="image/png" href="/assets/favicon.png">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="alternate" type="application/rss+xml" title="TechDcoded" href="/feed.xml">
<meta name="theme-color" content="#050a1e">
<meta property="og:site_name" content="TechDcoded">
<meta property="og:locale" content="en_IN">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{image}">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
<meta name="twitter:image" content="{image}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/site.css?v={CSS_V}">
{extra}
{METRICOOL}
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="/" aria-label="TechDcoded home"><picture><source srcset="/assets/logo.webp" type="image/webp"><img src="/assets/logo.png" alt="TechDcoded" width="620" height="143"></picture></a>
  <nav><a class="hide-m" href="/">Home</a><a href="/articles/">Articles</a><a class="hide-m" href="https://whatsapp.com/channel/0029Vb8OFIr5vKA1diUdno3a" target="_blank" rel="noopener">WhatsApp</a>
  <a class="btn-yt" href="https://www.youtube.com/@techdcoded?sub_confirmation=1" target="_blank" rel="noopener">▶ Subscribe</a></nav>
</div></header>
"""


def foot():
    links = "".join(f'<a href="{u}" target="_blank" rel="noopener">{n}</a>' for n, u in SOCIAL)
    return f"""<footer class="foot"><div class="wrap">
<nav><a href="/">Home</a><a href="/articles/">All articles</a>{links}</nav>
<p>© TechDcoded™ · You use the technology. We decode it. · <a href="mailto:business@techdcoded.com">business@techdcoded.com</a></p>
</div></footer>
</body>
</html>"""


def card(a, lazy=True):
    img = f"/assets/articles/{a['slug']}"
    return f"""<a class="card" href="/articles/{a['slug']}/" data-cat="{e(a['category'])}" data-q="{e((a['title'] + ' ' + a['meta_description'] + ' ' + a['category']).lower())}">
<picture style="display:block;width:100%"><source srcset="{img}.webp" type="image/webp"><img src="{img}.jpg" alt="{e(a.get('image_alt', a['title']))}" width="1200" height="630" style="display:block;width:100%;height:auto;aspect-ratio:1200/630;object-fit:cover"{' loading="lazy"' if lazy else ''}></picture>
<div class="cb"><span class="cat">{e(a['category'])}</span><h3>{e(a['title'])}</h3><p>{e(a['meta_description'])}</p><time datetime="{a['date']}">{fmt_date(a['date'])}</time></div></a>"""


# ---------------- article page ----------------
def render_body(md_text, images=None):
    md_text = re.sub(r"^# .*\n", "", md_text)
    md_text = re.sub(r"<\s*/?\s*(script|iframe|style)[^>]*>", "", md_text, flags=re.I)
    md_text, store = blocks.expand(md_text, images)
    md = markdown.Markdown(extensions=["tables", "sane_lists", "toc"], extension_configs={"toc": {"toc_depth": "2"}})
    body = md.convert(md_text)
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    body = blocks.restore(body, store)
    toc_items = [(t["id"], t["name"]) for t in md.toc_tokens]
    return body, toc_items


def related(a, arts, emb):
    others = [x for x in arts if x["slug"] != a["slug"]]
    v = emb.get(a["slug"])
    if v:
        others.sort(key=lambda x: cosine(v, emb.get(x["slug"], [0] * len(v))), reverse=True)
    else:
        others.sort(key=lambda x: (x["category"] != a["category"]))
    return others[:3]


def article_page(a, arts, emb):
    url = f"{SITE}/articles/{a['slug']}/"
    image = f"{SITE}/assets/articles/{a['slug']}.jpg"
    body, toc = render_body(a["body_md"], a.get("images"))
    wc = a.get("word_count") or len(re.findall(r"\w+", a["body_md"]))
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "BlogPosting", "headline": a["title"], "description": a["meta_description"], "image": [image],
         "datePublished": f"{a['date']}T{a.get('time', '07:00')}:00+05:30", "dateModified": f"{a.get('updated', a['date'])}T{a.get('time', '07:00')}:00+05:30",
         "author": {"@type": "Organization", "name": "TechDcoded", "url": SITE},
         "publisher": {"@type": "Organization", "name": "TechDcoded", "logo": {"@type": "ImageObject", "url": f"{SITE}/assets/logo-mark.png"}},
         "mainEntityOfPage": url, "articleSection": a["category"], "keywords": ", ".join([a.get("primary_keyword", "")] + a.get("secondary_keywords", [])),
         "wordCount": wc, "inLanguage": "en-IN"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Articles", "item": SITE + "/articles/"},
            {"@type": "ListItem", "position": 3, "name": a["title"], "item": url}]},
    ]}
    if a.get("faq"):
        ld["@graph"].append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in a["faq"]]})
    extra = f'<meta property="og:type" content="article"><meta property="article:published_time" content="{a["date"]}T{a.get("time", "07:00")}:00+05:30"><meta property="article:section" content="{e(a["category"])}">\n<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
    takeaways = "".join(f"<li>{e(t)}</li>" for t in a.get("key_takeaways", []))
    toc_html = "".join(f'<li><a href="#{i}">{n}</a></li>' for i, n in toc)
    faq = "".join(f"<details><summary>{e(f['q'])}</summary><p>{e(f['a'])}</p></details>" for f in a.get("faq", []))
    srcs = "".join(f'<li><a href="{e(s["url"])}" target="_blank" rel="nofollow noopener">{e(s["title"])}</a></li>' for s in a.get("sources", []))
    rel = "".join(card(r) for r in related(a, arts, emb))
    share_txt = e(f"{a['title']} {url}")
    img = f"/assets/articles/{a['slug']}"
    return head(f"{a['title']} | TechDcoded", a["meta_description"], url, image, extra) + f"""
<div class="progress" id="prog"></div>
<main class="narrow">
<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a><span>›</span><a href="/articles/">Articles</a><span>›</span>{e(a['category'])}</nav>
<article>
<span class="pill">{e(a['category'])}</span>
<h1 class="title">{e(a['title'])}</h1>
<div class="meta"><span>{e(a.get('author', 'TechDcoded Team'))}</span><span>·</span><time datetime="{a['date']}">{fmt_date(a['date'])}</time><span>·</span><span>{read_time(wc)} min read</span></div>
<div class="hero-img"><picture><source srcset="{img}.webp" type="image/webp"><img src="{img}.jpg" alt="{e(a.get('image_alt', a['title']))}" width="1200" height="630" fetchpriority="high"></picture></div>
<div class="answer"><b>Quick answer</b><p>{e(a['tldr'])}</p></div>
{f'<div class="takeaways"><h2>Key takeaways</h2><ul>{takeaways}</ul></div>' if takeaways else ''}
{f'<details class="toc" open><summary>In this article</summary><ul>{toc_html}</ul></details>' if toc_html else ''}
<div class="prose">{body}</div>
{f'<section class="faq"><h2>Frequently asked questions</h2>{faq}</section>' if faq else ''}
{f'<section class="sources"><h2>Sources</h2><ol>{srcs}</ol></section>' if srcs else ''}
</article>
<div class="share">Share:
<a href="https://wa.me/?text={share_txt}" target="_blank" rel="noopener">WhatsApp</a>
<a href="https://x.com/intent/post?text={share_txt}" target="_blank" rel="noopener">X</a>
<a href="https://www.linkedin.com/sharing/share-offsite/?url={e(url)}" target="_blank" rel="noopener">LinkedIn</a>
<button type="button" onclick="navigator.clipboard.writeText('{url}');this.textContent='Copied!'">Copy link</button></div>
<div class="cta"><div><b>Get one tech explainer a day</b><p>Short videos on YouTube, daily tech bites on WhatsApp.</p></div>
<div class="acts"><a class="btn-yt" href="https://www.youtube.com/@techdcoded?sub_confirmation=1" target="_blank" rel="noopener">▶ Subscribe</a><a class="btn-wa" href="https://whatsapp.com/channel/0029Vb8OFIr5vKA1diUdno3a" target="_blank" rel="noopener">Join WhatsApp</a></div></div>
{f'<section class="related"><h2>Keep reading</h2><div class="cards">{rel}</div></section>' if rel else ''}
</main>
<a class="totop" href="#" aria-label="Back to top">↑</a>
<script>
const pg=document.getElementById('prog'),tt=document.querySelector('.totop');
addEventListener('scroll',()=>{{const h=document.documentElement,p=h.scrollTop/(h.scrollHeight-h.clientHeight);pg.style.width=(p*100)+'%';tt.classList.toggle('on',h.scrollTop>900)}},{{passive:true}});
const io=new IntersectionObserver(es=>es.forEach(x=>{{if(x.isIntersecting){{x.target.classList.add('in');io.unobserve(x.target)}}}}),{{threshold:.15}});
document.querySelectorAll('.prose .vb').forEach(el=>io.observe(el));
</script>
""" + foot()


# ---------------- list page ----------------
def list_page(arts):
    cats = sorted({a["category"] for a in arts})
    chips = '<button class="chip on" data-c="">All</button>' + "".join(f'<button class="chip" data-c="{e(c)}">{e(c)}</button>' for c in cats)
    cards = "".join(card(a, lazy=i > 5) for i, a in enumerate(arts))
    ld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "TechDcoded Articles", "url": f"{SITE}/articles/",
          "hasPart": [{"@type": "BlogPosting", "headline": a["title"], "url": f"{SITE}/articles/{a['slug']}/"} for a in arts[:30]]}
    desc = "Simple, jargon-free explainers on AI, gadgets, fintech, space, how things work and the future of technology — a new article every day."
    return head("Tech Explained Simply — Articles | TechDcoded", desc, f"{SITE}/articles/", f"{SITE}/assets/og.jpg",
                f'<meta property="og:type" content="website"><script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>') + f"""
<main class="wrap">
<section class="list-hero"><h1>Tech, decoded daily</h1><p>{desc}</p></section>
<div class="tools"><input class="search" id="q" type="search" placeholder="Search articles…" aria-label="Search articles"><div class="chips" id="chips">{chips}</div></div>
<div class="cards" id="cards">{cards}</div>
<p class="empty" id="empty"{' style="display:block"' if not arts else ''}>{'New articles are coming soon — check back tomorrow!' if not arts else 'No articles match that search yet.'}</p>
</main>
<script>
const q=document.getElementById('q'),cs=[...document.querySelectorAll('#cards .card')];let cat='';
function f(){{const s=q.value.trim().toLowerCase();let n=0;cs.forEach(c=>{{const ok=(!cat||c.dataset.cat===cat)&&(!s||c.dataset.q.includes(s));c.style.display=ok?'':'none';n+=ok}});document.getElementById('empty').style.display=n?'none':'block'}}
q.oninput=f;document.getElementById('chips').onclick=ev=>{{const b=ev.target.closest('.chip');if(!b)return;document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));b.classList.add('on');cat=b.dataset.c;f()}};
</script>
""" + foot()


# ---------------- sitemap / feed / home ----------------
def sitemap(arts):
    today = now_ist().strftime("%Y-%m-%d")
    urls = [(f"{SITE}/", arts[0]["date"] if arts else today, "daily", "1.0"),
            (f"{SITE}/articles/", arts[0]["date"] if arts else today, "daily", "0.9")]
    urls += [(f"{SITE}/articles/{a['slug']}/", a.get("updated", a["date"]), "monthly", "0.8") for a in arts]
    body = "".join(f"<url><loc>{u}</loc><lastmod>{d}</lastmod><changefreq>{c}</changefreq><priority>{p}</priority></url>" for u, d, c, p in urls)
    (ROOT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n')


def feed(arts):
    items = ""
    for a in arts[:30]:
        dt = datetime.strptime(f"{a['date']} {a.get('time', '07:00')}", "%Y-%m-%d %H:%M").strftime("%a, %d %b %Y %H:%M:00 +0530")
        u = f"{SITE}/articles/{a['slug']}/"
        items += f"<item><title>{xesc(a['title'])}</title><link>{u}</link><guid>{u}</guid><pubDate>{dt}</pubDate><category>{xesc(a['category'])}</category><description>{xesc(a['meta_description'])}</description><enclosure url=\"{SITE}/assets/articles/{a['slug']}.jpg\" type=\"image/jpeg\" length=\"0\"/></item>"
    (ROOT / "feed.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>TechDcoded</title><link>{SITE}/</link><description>Technology explained simply — a new article every day.</description><language>en-in</language>{items}</channel></rss>\n')


def home_latest(arts):
    p = ROOT / "index.html"
    s = p.read_text(encoding="utf-8")
    if "<!--LATEST:START-->" not in s:
        return
    style = ("<style>.latest .la{display:grid;grid-template-columns:128px 1fr;gap:12px;align-items:center;padding:10px}"
             ".latest .la .th{position:relative;display:block;width:128px;aspect-ratio:1200/630;border-radius:9px;overflow:hidden;"
             "border:1px solid rgba(120,170,255,.25);box-shadow:0 6px 16px -6px rgba(0,0,0,.6)}"
             ".latest .la .th img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .35s}"
             ".latest .la:hover .th img{transform:scale(1.08)}"
             ".latest .la .th::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 55%,rgba(5,10,30,.55))}"
             ".latest .la .tx{display:flex;flex-direction:column;gap:3px;min-width:0}"
             ".latest .la .lt{font-size:.84rem;line-height:1.3;-webkit-line-clamp:3}"
             "@media(max-width:600px){.latest .la{grid-template-columns:110px 1fr}.latest .la .th{width:110px}}</style>")
    items = "".join(
        f'<a class="la" href="/articles/{a["slug"]}/"><span class="th"><img src="/assets/articles/{a["slug"]}.webp" '
        f'alt="{e(a.get("image_alt", a["title"]))}" width="1200" height="630" loading="lazy" '
        f'onerror="this.onerror=null;this.src=\'/assets/articles/{a["slug"]}.jpg\'"></span>'
        f'<span class="tx"><span class="lc">{e(a["category"])}</span><span class="lt">{e(a["title"])}</span>'
        f'<time datetime="{a["date"]}">{fmt_date(a["date"])}</time></span></a>' for a in arts[:3])
    block = f'<!--LATEST:START-->{style}<section class="latest" aria-label="Latest articles"><div class="lh"><h2>Latest articles</h2><a href="/articles/">View all →</a></div><div class="lg">{items}</div></section><!--LATEST:END-->' if arts else "<!--LATEST:START--><!--LATEST:END-->"
    s = re.sub(r"<!--LATEST:START-->.*?<!--LATEST:END-->", lambda m: block, s, flags=re.S)
    p.write_text(s, encoding="utf-8")


def main():
    arts, emb = load_articles(), load_embeddings()
    OUT.mkdir(exist_ok=True)
    for a in arts:
        make_image(a)
        d = OUT / a["slug"]; d.mkdir(exist_ok=True)
        (d / "index.html").write_text(article_page(a, arts, emb), encoding="utf-8")
    (OUT / "index.html").write_text(list_page(arts), encoding="utf-8")
    sitemap(arts); feed(arts); home_latest(arts)
    cleanup(arts, emb)
    print(f"Built {len(arts)} articles")


def cleanup(arts, emb):
    """Delete pages, pictures and memory of articles whose file was removed from content/articles/."""
    import shutil
    from common import save_embeddings
    slugs = {a["slug"] for a in arts}
    for d in OUT.iterdir():
        if d.is_dir() and d.name not in slugs:
            shutil.rmtree(d); print("removed page:", d.name)
    if IMG.exists():
        for f in IMG.iterdir():
            base = re.sub(r"-(\d+|cover)$", "", f.stem)  # pictures are saved as <slug>-1, <slug>-cover ...
            if f.stem not in slugs and base not in slugs:
                f.unlink(); print("removed image:", f.name)
    stale = [k for k in emb if k not in slugs]
    if stale:
        for k in stale:
            emb.pop(k)
        save_embeddings(emb)


if __name__ == "__main__":
    main()
