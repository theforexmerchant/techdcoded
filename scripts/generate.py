"""Daily article generator for techdcoded.com.

Picks a fresh topic from data/topics.txt, blocks near-duplicates, researches it with
Gemini + Google Search grounding, writes an SEO article, runs a quality gate, and saves
content/articles/<date>-<slug>.json. Exits non-zero (so GitHub emails you) if quality fails.

Env: GEMINI_API_KEY (required), GEMINI_MODEL (optional; auto-picks newest flash model),
     GEMINI_EMBED_MODEL (default gemini-embedding-001)
"""
import json, os, random, re, sys, time
import xml.etree.ElementTree as ET
import requests
import media
from common import (CONTENT, DATA, load_topics, load_articles, load_embeddings, save_embeddings,
                    cosine, jaccard, slugify, now_ist)

API = "https://generativelanguage.googleapis.com/v1beta/models"
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "") or "auto"
EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
SIM_LIMIT = 0.86      # embedding cosine above this = too similar to an existing article
JAC_LIMIT = 0.55      # title word-overlap above this = too similar
MIN_WORDS = 1100

STYLE = """You write for TechDcoded (techdcoded.com), an Indian tech-explainer brand: "You use the technology. We decode it."
Audience: curious Indian readers (students, professionals, general public) — smart but not engineers.
Voice: clear, curious, engaging, jargon-free (explain any jargon in plain words), confident, no hype, no fluff.
Use Indian context where it genuinely fits (₹, UPI, Indian brands, ISRO, Indian cities) but keep it globally useful.
Never invent statistics, quotes, prices, dates or studies. Only use facts present in the research notes; if unsure, say it generally.
For crime, hacking, scams, weapons or security topics: explain what happened and how to stay safe at a high level only —
never give step-by-step instructions that could help someone cause harm.
Never mention that you are an AI. No clickbait that the article doesn't deliver on."""


# ---------------- Gemini helpers ----------------
def gemini(prompt, *, search=False, json_mode=False, temperature=0.7, retries=4):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}}
    if search:
        body["tools"] = [{"google_search": {}}]
    elif json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    url = f"{API}/{MODEL}:generateContent"
    last = ""
    for i in range(retries):
        try:
            r = requests.post(url, json=body, headers={"x-goog-api-key": KEY}, timeout=180)
        except requests.RequestException as ex:
            last = f"network error {ex}"; time.sleep(10 * (i + 1)); continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"{r.status_code}: {r.text[:250]}"
            print(f"  Gemini busy/limited ({'search' if search else 'text'}) try {i + 1}: {last}")
            # a used-up quota won't recover in minutes — stop waiting
            if r.status_code == 429 and ("quota" in r.text.lower() or "exhausted" in r.text.lower()) and i >= 1:
                break
            time.sleep(10 * (i + 1)); continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        if not text.strip():
            last = f"empty reply (finishReason={cand.get('finishReason')}, feedback={j.get('promptFeedback')})"
            print("  Gemini", last); time.sleep(5); continue
        return text, cand.get("groundingMetadata", {})
    raise RuntimeError(f"Gemini request failed after retries — last error: {last}")


def resolve_model():
    """Use GEMINI_MODEL if it exists; otherwise pick the newest stable 'flash' text model available to this key.
    Google retires model names over time, so this keeps the pipeline working without edits."""
    global MODEL
    if MODEL != "auto":
        r = requests.get(f"{API}/{MODEL}", headers={"x-goog-api-key": KEY}, timeout=30)
        if r.ok:
            print("Using model:", MODEL); return
        print(f"Model {MODEL} unavailable ({r.status_code}); auto-selecting a current one.")
    models, token = [], None
    while True:
        r = requests.get(API, headers={"x-goog-api-key": KEY}, params={"pageSize": 200, **({"pageToken": token} if token else {})}, timeout=30)
        if r.status_code in (400, 401, 403):
            sys.exit(f"GEMINI_API_KEY was rejected ({r.status_code}): {r.text[:300]}")
        r.raise_for_status()
        j = r.json(); models += j.get("models", []); token = j.get("nextPageToken")
        if not token:
            break
    bad = ("image", "tts", "audio", "live", "embedding", "vision", "thinking-exp", "learnlm", "gemma", "robotics", "computer")
    def score(m):
        n = m["name"].split("/")[-1]
        ver = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", n)[:1]] or [0]
        return (("preview" not in n and "exp" not in n), ver[0], "lite" not in n, "latest" in n)
    cands = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])
             and "flash" in m["name"] and not any(b in m["name"] for b in bad)]
    if not cands:
        cands = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]
                 and not any(b in m["name"] for b in bad)]
    if not cands:
        sys.exit("No usable Gemini text model found for this API key.")
    MODEL = max(cands, key=score)["name"].split("/")[-1]
    print("Using model:", MODEL)


def gemini_json(prompt, temperature=0.6):
    for _ in range(3):
        text, _ = gemini(prompt, json_mode=True, temperature=temperature)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
    raise RuntimeError("Model did not return valid JSON")


def embed(text):
    url = f"{API}/{EMBED_MODEL}:embedContent"
    body = {"content": {"parts": [{"text": text}]}, "outputDimensionality": 256}
    for i in range(4):
        r = requests.post(url, json=body, headers={"x-goog-api-key": KEY}, timeout=60)
        if r.status_code in (429, 500, 503):
            time.sleep(10 * (i + 1)); continue
        r.raise_for_status()
        return [round(v, 5) for v in r.json()["embedding"]["values"]]
    raise RuntimeError("Embedding failed")


# ---------------- Topic selection ----------------
# Order every day:  1) best trending tech topic (explainer angle)  2) unused idea from the idea bank
# (your list + ideas the system invents itself)  3) invent brand-new ideas on the spot.
# The idea bank refills itself automatically, so it never runs out.
TREND_MIN_FIT = 8          # trending topic must score >= this (1-10) for fit with TechDcoded
AUTO_REFILL_BELOW = 40     # when fewer unused ideas remain, invent more
AUTO_REFILL_COUNT = 30
HT = "{https://trends.google.com/trending/rss}"


def google_trends_india():
    """Google Trends daily trending searches for India, with the headline that explains each trend."""
    try:
        r = requests.get("https://trends.google.com/trending/rss?geo=IN", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 techdcoded-bot"})
        r.raise_for_status()
        out = []
        for it in ET.fromstring(r.content).iter("item"):
            q = it.findtext("title", "").strip()
            news = [n.findtext(f"{HT}news_item_title", "").strip() for n in it.iter(f"{HT}news_item")]
            out.append(f"{q} — {news[0]}" if news and news[0] else q)
        return out
    except Exception as ex:
        print("Google Trends failed:", ex)
        return []


def hacker_news(n=25):
    """Top stories on Hacker News (global tech crowd)."""
    try:
        ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=20).json()[:n]
        return [requests.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=10).json().get("title", "")
                for i in ids]
    except Exception as ex:
        print("Hacker News failed:", ex)
        return []


def fetch_trends():
    heads = google_trends_india() + hacker_news()
    try:  # live search view of what's trending, in case RSS is thin
        txt, _ = gemini("Using Google Search, list the 15 most talked-about technology, AI, gadget, fintech, space and "
                        "cybersecurity stories in India and globally in the last 48 hours. One line each, no commentary.",
                        search=True, temperature=0.2)
        heads += [l.strip("-•* ").strip() for l in txt.splitlines() if len(l.strip()) > 15]
    except Exception as ex:
        print("search trends failed:", ex)
    seen, out = set(), []
    for h in heads:
        k = h.lower()[:80]
        if h and k not in seen:
            seen.add(k); out.append(h)
    return out[:120]


def screen(cands, cat, idea, source, existing_titles, emb):
    """Return the first candidate that is not a near-duplicate of anything published."""
    for c in cands:
        t = c.get("title", "").strip()
        if not t:
            continue
        if any(jaccard(t, x) > JAC_LIMIT for x in existing_titles):
            print("skip (title overlap):", t); continue
        vec = embed(f"{t}. {c.get('angle', '')}")
        top = max((cosine(vec, v) for v in emb.values()), default=0)
        if top > SIM_LIMIT:
            print(f"skip (similar {top:.2f}):", t); continue
        return {"category": c.get("category") or cat, "idea": c.get("idea") or idea, "source": source,
                "trend_context": c.get("trend_context", ""), **c, "vector": vec}
    return None


def pick_trending(articles, emb, categories):
    heads = fetch_trends()
    if not heads:
        return None
    existing = [a["title"] for a in articles]
    prompt = f"""{STYLE}

Below are today's trending headlines and search topics. Pick the 3 trending subjects that best fit TechDcoded
(technology, AI, gadgets, internet, fintech, space, science, cybersecurity, EVs, how things work).
Ignore politics, entertainment gossip, sports scores, crime unrelated to tech, stock tips, and anything too thin to explain.
For each, write an EXPLAINER article idea that stays useful for months (how it works / what it means / what to do),
NOT a news report. Example: trending "new iPhone launched" -> "How the iPhone's New Chip Actually Works".
Title: 40-65 chars, phrased the way people search on Google.
Category: choose the closest from {json.dumps(categories)} (or a short new one if none fit).
fit: 1-10 for how well it suits TechDcoded AND how much lasting search interest it will have.
Do NOT repeat these published articles: {json.dumps(existing[:60], ensure_ascii=False)}

Headlines:
{json.dumps(heads, ensure_ascii=False)}

Return JSON: [{{"title": "...", "primary_keyword": "...", "angle": "...", "category": "...", "fit": n,
"trend_context": "one line: what is trending and why, with the date if known", "idea": "short topic name"}}]"""
    try:
        cands = gemini_json(prompt, temperature=0.5)
    except Exception as ex:
        print("trend pick failed:", ex); return None
    cands = sorted([c for c in cands if c.get("fit", 0) >= TREND_MIN_FIT], key=lambda c: -c.get("fit", 0))
    print("Trending candidates:", [(c.get("title"), c.get("fit")) for c in cands])
    return screen(cands, "", "", "trending", existing, emb)


def invent_ideas(articles, n=AUTO_REFILL_COUNT):
    """Invent fresh ideas in the same spirit as the idea bank and append them to data/ideas_auto.txt."""
    topics = load_topics()
    cats = sorted({c for c, _ in topics})
    sample = random.sample(topics, min(60, len(topics)))
    prompt = f"""{STYLE}

You plan content for TechDcoded. Here are example ideas from our idea bank (category | idea):
{chr(10).join(f"{c} | {i}" for c, i in sample)}

Invent {n} NEW article ideas in the same spirit — curiosity-driven explainers, "how it works", comparisons,
hidden costs, myths, history, future, careers, India-relevant tech. Spread them across these categories and feel free
to add up to 3 new categories: {json.dumps(cats)}
Each idea must be clearly different from every example and from these published articles:
{json.dumps([a["title"] for a in articles][:120], ensure_ascii=False)}
Prefer ideas with steady Google search demand. No politics, no medical or financial advice.

Return JSON: [{{"category": "...", "idea": "..."}}]"""
    try:
        new = gemini_json(prompt, temperature=1.0)
    except Exception as ex:
        print("idea invention failed:", ex); return 0
    known = {i.lower() for _, i in topics}
    lines = []
    for x in new:
        c, i = x.get("category", "").strip(), x.get("idea", "").strip()
        if c and i and i.lower() not in known and not any(jaccard(i, k) > 0.7 for k in known):
            known.add(i.lower()); lines.append((c, i))
    if lines:
        p = DATA / "ideas_auto.txt"
        prev = p.read_text(encoding="utf-8") if p.exists() else "# Ideas invented automatically by the daily generator.\n"
        block = f"\n# added {now_ist().strftime('%Y-%m-%d')}\n" + "".join(f"## {c}\n{i}\n" for c, i in lines)
        p.write_text(prev + block, encoding="utf-8")
    print(f"Invented {len(lines)} new ideas")
    return len(lines)


def pick_from_bank(articles, emb):
    used = {a.get("idea", "") for a in articles}
    fresh = [t for t in load_topics() if t[1] not in used]
    if len(fresh) < AUTO_REFILL_BELOW:
        invent_ideas(articles)
        fresh = [t for t in load_topics() if t[1] not in used]
    cat_count = {}
    for a in articles:
        cat_count[a.get("category", "")] = cat_count.get(a.get("category", ""), 0) + 1
    random.shuffle(fresh)
    fresh.sort(key=lambda t: cat_count.get(t[0], 0))  # least-covered categories first
    existing = [a["title"] for a in articles]
    for cat, idea in fresh[:6]:
        same_cat = [a["title"] for a in articles if a.get("category") == cat]
        prompt = f"""{STYLE}

Propose 5 distinct blog article titles for the TechDcoded category "{cat}" based on this idea: "{idea}".
Each title must target something people actually search on Google (a question or clear phrase), be specific,
40-65 characters, and promise real value. Prefer evergreen angles.
Do NOT overlap with these existing TechDcoded articles:
{json.dumps(same_cat[-40:] + existing[:40], ensure_ascii=False)}

Return JSON: [{{"title": "...", "primary_keyword": "...", "angle": "one line on what the article covers"}}]"""
        try:
            cands = gemini_json(prompt, temperature=0.9)
        except Exception as ex:
            print("title proposal failed:", ex); continue
        got = screen(cands, cat, idea, "idea-bank", existing, emb)
        if got:
            return got
    return None


def pick_topic(articles, emb):
    categories = sorted({c for c, _ in load_topics()})
    if os.environ.get("TRENDING", "on") != "off":
        t = pick_trending(articles, emb, categories)
        if t:
            return t
        print("No strong trending topic today — using the idea bank.")
    t = pick_from_bank(articles, emb)
    if t:
        return t
    invent_ideas(articles)  # last resort: fresh ideas right now
    t = pick_from_bank(articles, emb)
    if t:
        return t
    raise RuntimeError("No non-duplicate topic found today")


# ---------------- Research / write / review ----------------
def research(topic):
    prompt = f"""Research this article topic using Google Search: "{topic['title']}" ({topic['angle']}).
Produce detailed factual research notes for a writer: definitions, how it works step by step, key numbers with
dates and who reported them, recent developments (last 12-18 months), Indian context if relevant, common myths,
and questions people ask. Bullet points. Be precise; mark anything uncertain as uncertain."""
    try:
        text, meta = gemini(prompt, search=True, temperature=0.3)
    except RuntimeError as ex:
        # Live Google Search grounding unavailable (e.g. free-tier search quota used up).
        # Fall back to the model's own knowledge, restricted to well-established facts.
        print("Search grounding unavailable, using knowledge-only research:", ex)
        text, meta = gemini(f"""Write detailed factual research notes for a writer on: "{topic['title']}" ({topic['angle']}).
Cover definitions, how it works step by step, history, real-world and Indian examples, common myths and questions people ask.
Use ONLY well-established facts you are highly confident about. Do NOT include recent statistics, prices, dates of the
last 2 years, or specific figures unless they are long-standing and widely known. Mark anything uncertain as uncertain.
Bullet points.""", temperature=0.2)
        meta = {"_ungrounded": True}
    sources, seen = [], set()
    for ch in meta.get("groundingChunks", []):
        w = ch.get("web", {})
        title, uri = w.get("title", "").strip(), w.get("uri", "")
        if uri and title and title not in seen:
            seen.add(title)
            try:  # grounding links are temporary redirects; store the real page URL
                uri = requests.head(uri, allow_redirects=True, timeout=15).url or uri
            except requests.RequestException:
                pass
            sources.append({"title": title, "url": uri})
    return text, sources[:8], not meta.get("_ungrounded")


WRITE_SPEC = """Return JSON with exactly these keys:
{
 "title": "SEO title, 40-65 chars, contains the primary keyword naturally",
 "meta_description": "140-160 chars, compelling, contains the primary keyword",
 "primary_keyword": "...",
 "secondary_keywords": ["5-8 related search phrases"],
 "tldr": "2-3 sentence direct answer to the title question (this appears at the top)",
 "key_takeaways": ["4-5 short bullet takeaways"],
 "body_md": "The article body in Markdown (rules below)",
 "faq": [{"q": "real question people search", "a": "40-80 word answer"}],
 "image_alt": "short description of the topic for the featured image alt text",
 "visuals": [{"after_heading": "exact text of the ## heading this picture belongs under",
              "type": "photo or illustration",
              "query": "2-4 word stock-photo search, concrete and visual (e.g. 'data center servers')",
              "prompt": "for illustrations: one sentence describing a concept scene, no text in image",
              "caption": "one helpful sentence explaining what the reader sees",
              "alt": "accessible description"}]
}

body_md rules: 1300-1800 words. Start with an engaging intro paragraph (no heading). Use ## for 6-9 main sections and
### for sub-points. Short paragraphs (2-4 sentences), bullet lists, **bold** for key terms, one Markdown table where a
comparison or data fits, a section with a real-world or Indian example, and end with '## The Bottom Line'.
Do NOT include an H1, the FAQ, sources or key takeaways in body_md.

MAKE IT VISUAL. The page renders these blocks as colourful diagrams and cards, so include 5-8 of them spread through
the article (each on its own lines, exactly this syntax, closing ::: on its own line):

:::steps Short diagram title        <- REQUIRED at least once: a process/flow diagram, 3-6 steps
1. Step name | one short line
:::
:::stats Short title                 <- 2-4 big numbers. ONLY numbers that appear in the research notes
₹1 lakh | what the number means
:::
:::didyouknow                        <- surprising fact (1-2 sentences)
text
:::
:::tip Optional title                <- practical advice for the reader
text
:::
:::warning Optional title            <- a common mistake, risk or scam to avoid
text
:::
:::myth                              <- a common misconception
Myth: ...
Fact: ...
:::
:::compare Thing A vs Thing B        <- side-by-side comparison, 3-5 rows
point about A | point about B
:::
:::timeline Short title              <- history or evolution, 3-6 entries
2016 | what happened
:::
:::keyterm Term name                 <- plain-English definition of one jargon word
text
:::
:::quote                             <- one memorable line worth highlighting
text
:::

visuals: 2-3 items placed under different ## headings. Use "photo" for real objects/places/people-at-work,
"illustration" for abstract concepts. FAQ: 5 items. Write in English."""


def write(topic, notes, feedback=None, previous=None):
    extra = ""
    if feedback:
        extra = f"\n\nAn editor reviewed your previous draft and found these problems — fix ALL of them:\n{json.dumps(feedback, ensure_ascii=False)}\n\nPrevious draft:\n{json.dumps(previous, ensure_ascii=False)[:20000]}"
    prompt = f"""{STYLE}

Write a complete, genuinely helpful, SEO-optimised article.
Working title: "{topic['title']}"  |  Primary keyword: "{topic.get('primary_keyword', '')}"  |  Angle: {topic['angle']}
Category: {topic['category']}
{("This topic is trending right now: " + topic['trend_context'] + chr(10) + "Open with a short hook about why it is in the news, then deliver an evergreen explainer that stays useful for months. Do not write a news report.") if topic.get('source') == 'trending' else ''}

Research notes (your ONLY source of facts):
{notes}
{extra}

{WRITE_SPEC}"""
    return gemini_json(prompt, temperature=0.7)


def mechanical_issues(a):
    issues = []
    body = a.get("body_md", "")
    wc = len(re.findall(r"\w+", body))
    if wc < MIN_WORDS: issues.append(f"Body is only {wc} words; must be at least {MIN_WORDS + 200}.")
    if len(re.findall(r"^## ", body, re.M)) < 5: issues.append("Needs at least 6 '## ' sections.")
    if len(a.get("faq", [])) < 4: issues.append("Needs 5 FAQ items.")
    if not (30 <= len(a.get("title", "")) <= 70): issues.append("Title must be 40-65 characters.")
    if not (110 <= len(a.get("meta_description", "")) <= 170): issues.append("Meta description must be 140-160 characters.")
    if re.search(r"as an ai|language model|i cannot|\[insert|lorem ipsum", body, re.I): issues.append("Contains AI boilerplate/placeholder text.")
    if re.search(r"^# ", body, re.M): issues.append("Remove the H1 from body_md.")
    nblocks = len(re.findall(r"^:::[ \t]*[a-zA-Z]+", body, re.M))
    if nblocks < 4: issues.append(f"Only {nblocks} visual blocks; include 5-8 (::: blocks), including at least one :::steps diagram.")
    if not re.search(r"^:::[ \t]*steps", body, re.M): issues.append("Add at least one :::steps process diagram.")
    return issues, wc


def review(article, notes):
    prompt = f"""You are a strict senior editor for a tech-explainer site that must rank on Google and never publish errors.
Review this article against the research notes. Score 1-10 each: accuracy (claims supported by notes, no invented numbers),
helpfulness (answers the title fully, specific, not generic), readability (clear for non-experts), originality (fresh insight,
not generic filler), safety (no harmful instructions). List concrete problems to fix.

Research notes:
{notes[:12000]}

Article:
{json.dumps(article, ensure_ascii=False)[:24000]}

Return JSON: {{"accuracy": n, "helpfulness": n, "readability": n, "originality": n, "safety": n, "problems": ["..."]}}"""
    return gemini_json(prompt, temperature=0.2)


def passes(r):
    return (min(r.get("accuracy", 0), r.get("safety", 0)) >= 8
            and min(r.get("helpfulness", 0), r.get("readability", 0), r.get("originality", 0)) >= 7)


def main():
    if not KEY:
        sys.exit("GEMINI_API_KEY is not set")
    resolve_model()
    articles, emb = load_articles(), load_embeddings()
    today = now_ist().strftime("%Y-%m-%d")
    if any(a["date"] == today for a in articles) and "--force" not in sys.argv:
        print("Article already published today; nothing to do."); return

    missing = [a for a in articles if a["slug"] not in emb]  # e.g. hand-written articles
    for a in missing:
        emb[a["slug"]] = embed(f"{a['title']}. {a.get('meta_description', '')}")
    if missing:
        save_embeddings(emb)

    topic = pick_topic(articles, emb)
    print("Topic:", topic["title"], "|", topic["category"])
    notes, sources, grounded = research(topic)
    if not grounded and topic.get("source") == "trending":
        # news-driven topics need live facts; switch to an evergreen idea instead
        print("Trending topic needs live search; switching to an evergreen idea today.")
        topic = pick_from_bank(load_articles(), emb) or topic
        print("Topic:", topic["title"], "|", topic["category"])
        notes, sources, grounded = research(topic)

    art = write(topic, notes)
    for attempt in range(2):
        issues, wc = mechanical_issues(art)
        rev = review(art, notes)
        print(f"Review attempt {attempt + 1}:", {k: v for k, v in rev.items() if k != 'problems'}, "words:", wc)
        if not issues and passes(rev):
            break
        if attempt == 1:
            sys.exit("Quality gate failed twice — skipping today. Problems: " + json.dumps(issues + rev.get("problems", []))[:1500])
        art = write(topic, notes, feedback=issues + rev.get("problems", []), previous=art)

    slug = slugify(art["title"])
    existing_slugs = {a["slug"] for a in articles}
    if slug in existing_slugs:
        slug = f"{slug}-{today.replace('-', '')}"
    now = now_ist()
    record = {
        "slug": slug, "date": today, "time": now.strftime("%H:%M"), "updated": today,
        "title": art["title"].strip(), "meta_description": art["meta_description"].strip(),
        "category": topic["category"], "idea": topic["idea"], "source": topic.get("source", "idea-bank"),
        "primary_keyword": art.get("primary_keyword", topic.get("primary_keyword", "")),
        "secondary_keywords": art.get("secondary_keywords", []),
        "tldr": art["tldr"].strip(), "key_takeaways": art.get("key_takeaways", []),
        "body_md": art["body_md"].strip(), "faq": art.get("faq", []),
        "image_alt": art.get("image_alt", art["title"]), "sources": sources,
        "images": media.get_visuals(art.get("visuals", []), slug),
        "word_count": wc, "review": {k: v for k, v in rev.items() if k != "problems"},
        "author": "TechDcoded Team",
    }
    (CONTENT / f"{today}-{slug}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    emb[slug] = topic["vector"]
    save_embeddings(emb)
    print("Saved:", slug)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"slug={slug}\ntitle={record['title']}\n")


if __name__ == "__main__":
    main()
