"""Parse the plain-text article format the AI writes (### LABEL ### sections).

Plain labelled text is far more reliable than one giant JSON object: small slips (a stray quote,
a cut-off line) no longer break the whole article, and any AI model can follow it.
"""
import re

LABELS = {"TITLE", "META", "KEYWORD", "SECONDARY", "TLDR", "TAKEAWAYS", "BODY", "FAQ", "IMAGE_ALT", "VISUALS", "END"}


def parse_article(text):
    text = re.sub(r"^```\w*\s*|```\s*$", "", text.strip())
    parts, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*#{2,4}\s*([A-Za-z_ ]+?)\s*#{2,4}\s*$", line)
        if m and m.group(1).strip().upper().replace(" ", "_") in LABELS:
            cur = m.group(1).strip().upper().replace(" ", "_")
            parts[cur] = []
            continue
        if cur and cur != "END":
            parts[cur].append(line)

    def g(k):
        return "\n".join(parts.get(k, [])).strip()

    faq, q = [], None
    for line in g("FAQ").splitlines():
        line = line.strip()
        if re.match(r"^\**Q\d*[:.]", line):
            q = re.sub(r"^\**Q\d*[:.]\**\s*", "", line)
        elif re.match(r"^\**A\d*[:.]", line) and q:
            faq.append({"q": q, "a": re.sub(r"^\**A\d*[:.]\**\s*", "", line)})
            q = None
        elif faq and line and not q:
            faq[-1]["a"] += " " + line

    visuals = []
    for line in g("VISUALS").splitlines():
        f = [x.strip() for x in line.strip("-* ").split("|")]
        if len(f) >= 3 and f[0]:
            typ = "illustration" if "illus" in f[1].lower() else "photo"
            cap = f[3] if len(f) > 3 else ""
            visuals.append({"after_heading": f[0].lstrip("# "), "type": typ, "query": f[2], "prompt": f[2],
                            "caption": cap, "alt": cap or f[2]})

    title = g("TITLE").splitlines()[0].strip(' *#"') if g("TITLE") else ""
    art = {
        "title": title,
        "meta_description": " ".join(g("META").split()),
        "primary_keyword": (g("KEYWORD").splitlines() or [""])[0].strip(),
        "secondary_keywords": [x.strip() for x in g("SECONDARY").replace("\n", ",").split(",") if x.strip()][:8],
        "tldr": " ".join(g("TLDR").split()),
        "key_takeaways": [re.sub(r"^[-*•\d.)\s]+", "", x).strip() for x in g("TAKEAWAYS").splitlines() if x.strip()][:6],
        "body_md": g("BODY"),
        "faq": faq[:6],
        "image_alt": g("IMAGE_ALT"),
        "visuals": visuals[:3],
    }
    if not art["title"] or len(art["body_md"]) < 1500:
        raise RuntimeError("Article reply was incomplete or not in the expected format")
    return art


def to_text(a):
    """Turn an article dict back into the labelled format (used when asking for a rewrite)."""
    faq = "\n".join(f"Q: {x['q']}\nA: {x['a']}" for x in a.get("faq", []))
    vis = "\n".join(f"{v.get('after_heading','')} | {v.get('type','photo')} | {v.get('query','')} | {v.get('caption','')}"
                    for v in a.get("visuals", []))
    tk = "\n".join(f"- {t}" for t in a.get("key_takeaways", []))
    return (f"### TITLE ###\n{a.get('title','')}\n### META ###\n{a.get('meta_description','')}\n"
            f"### KEYWORD ###\n{a.get('primary_keyword','')}\n### SECONDARY ###\n{', '.join(a.get('secondary_keywords', []))}\n"
            f"### TLDR ###\n{a.get('tldr','')}\n### TAKEAWAYS ###\n{tk}\n### BODY ###\n{a.get('body_md','')}\n"
            f"### FAQ ###\n{faq}\n### IMAGE_ALT ###\n{a.get('image_alt','')}\n### VISUALS ###\n{vis}\n### END ###")
