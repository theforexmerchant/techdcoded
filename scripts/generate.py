"""Daily article generator for techdcoded.com.

Picks a fresh topic from data/topics.txt, blocks near-duplicates, researches it with
Gemini + Google Search grounding, writes an SEO article, runs a quality gate, and saves
content/articles/<date>-<slug>.json. Exits non-zero (so GitHub emails you) if quality fails.

Env: GEMINI_API_KEY (required), GEMINI_MODEL (optional; auto-picks newest flash model),
     GEMINI_EMBED_MODEL (default gemini-embedding-001)
"""
import json, os, random, re, sys, time
import xml.etree.ElementTree as ET
sys.stdout.reconfigure(line_buffering=True)
import requests
import media
from article_format import parse_article, to_text
from common import (CONTENT, DATA, load_topics, load_articles, load_embeddings, save_embeddings,
                    cosine, jaccard, slugify, now_ist)

API = "https://generativelanguage.googleapis.com/v1beta/models"
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "") or "auto"
FALLBACKS = []   # other usable models, tried when the main one is overloaded
SEARCH_OK = True # turned off for the rest of the run once search quota is exhausted
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
def _gemini(prompt, *, search=False, json_mode=False, temperature=0.7, retries=2):
    """Call Gemini. If the model stays overloaded (503/429), move on to the next available model."""
    global MODEL, SEARCH_OK
    if search and not SEARCH_OK:
        raise RuntimeError("search disabled for this run (quota exhausted earlier)")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 16384}}
    if search:
        body["tools"] = [{"google_search": {}}]
    elif json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    last, switches = "", 0
    while True:
        url = f"{API}/{MODEL}:generateContent"
        quota = False
        for i in range(retries):
            try:
                r = requests.post(url, json=body, headers={"x-goog-api-key": KEY}, timeout=180)
            except requests.RequestException as ex:
                last = f"network error {ex}"; time.sleep(10 * (i + 1)); continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{MODEL} {r.status_code}: {r.text[:200]}"
                print(f"  Gemini busy/limited ({'search' if search else 'text'}) try {i + 1}: {last}")
                quota = r.status_code == 429 and ("quota" in r.text.lower() or "exhausted" in r.text.lower())
                if quota and search:
                    SEARCH_OK = False
                    raise RuntimeError(f"search quota exhausted: {last}")
                if quota and i >= 1:
                    break
                time.sleep(15 * (i + 1)); continue
            if r.status_code in (403, 404):
                # model retired / not offered to this account: drop it, jump to Google's suggestion if given
                last = f"{MODEL} {r.status_code}: {r.text[:200]}"
                print(f"  Model not usable: {last}")
                hint = re.search(r"use models/([\w.\-]+)", r.text)
                if hint and hint.group(1) != MODEL:
                    FALLBACKS.insert(0, hint.group(1))
                break
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
            j = r.json()
            cand = (j.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
            if not text.strip():
                last = f"empty reply (finishReason={cand.get('finishReason')}, feedback={j.get('promptFeedback')})"
                print("  Gemini", last); time.sleep(5); continue
            return text, cand.get("groundingMetadata", {})
        while FALLBACKS and FALLBACKS[0] == MODEL:
            FALLBACKS.pop(0)
        if FALLBACKS and switches < 3:
            MODEL = FALLBACKS.pop(0); switches += 1
            print(f"  Switching to backup model: {MODEL}")
            continue
        raise RuntimeError(f"Gemini request failed after retries — last error: {last}")


# ---------------- Free backup AIs ----------------
# Order: Gemini (free) -> GitHub Models (free, built into GitHub Actions) -> Groq (free key, optional).
# If one is overloaded or refuses, the next one answers. Live Google Search research stays Gemini-only.
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
DEAD = set()          # providers that failed hard this run — skipped for the remaining calls
_MODEL_CACHE = {}


def _pick(ids, prefs, avoid=("embed", "whisper", "tts", "guard", "vision", "audio", "image", "speech", "moderation", "nano")):
    ids = [i for i in ids if not any(a in i.lower() for a in avoid)]
    out = []
    for p in prefs:
        out += [i for i in ids if re.search(p, i, re.I) and i not in out]
    return out


def _models_for(provider):
    if provider in _MODEL_CACHE:
        return _MODEL_CACHE[provider]
    ids = []
    try:
        if provider == "github":
            r = requests.get("https://models.github.ai/catalog/models", headers={"Authorization": f"Bearer {GH_TOKEN}"}, timeout=30)
            ids = [m.get("id", "") for m in r.json()] if r.ok else []
            ids = _pick(ids, [r"gpt-5(?!.*nano)", r"gpt-4\.1(?!.*nano)", r"gpt-4o", r"deepseek-v3", r"llama-4", r"llama-3\.3-70b"]) or \
                  ["openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/gpt-4o-mini"]
        elif provider == "groq":
            r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=30)
            ids = [m.get("id", "") for m in r.json().get("data", [])] if r.ok else []
            ids = _pick(ids, [r"gpt-oss-120b", r"kimi", r"llama-4-maverick", r"llama-3\.3-70b", r"qwen", r"llama"]) or \
                  ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    except Exception as ex:
        print(f"  {provider} model list failed: {ex}")
    _MODEL_CACHE[provider] = ids[:4]
    return _MODEL_CACHE[provider]


def _openai_compat(provider, prompt, json_mode, temperature):
    url, key, max_out = {
        "github": ("https://models.github.ai/inference/chat/completions", GH_TOKEN, 4000),
        "groq": ("https://api.groq.com/openai/v1/chat/completions", GROQ_KEY, 8000),
    }[provider]
    last = ""
    for model in _models_for(provider):
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "max_tokens": max_out}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            try:
                r = requests.post(url, json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=180)
            except requests.RequestException as ex:
                last = f"{model} network {ex}"; time.sleep(5); continue
            if r.status_code == 400 and "response_format" in body:
                body.pop("response_format"); continue       # model doesn't support JSON mode; ask plainly
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{model} {r.status_code}: {r.text[:160]}"; print(f"  {provider} busy: {last}"); time.sleep(8); continue
            if r.status_code >= 400:
                last = f"{model} {r.status_code}: {r.text[:160]}"; print(f"  {provider} refused: {last}"); break
            try:
                text = r.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError):
                text = ""
            if text.strip():
                return text
            last = f"{model} empty reply"
    raise RuntimeError(f"{provider} failed — {last}")


def gemini(prompt, *, search=False, json_mode=False, temperature=0.7):
    """Ask an AI. Search requests need Gemini; everything else falls back across free providers."""
    if search:
        return _gemini(prompt, search=True, temperature=temperature)
    order = [p.strip() for p in os.environ.get("AI_ORDER", "gemini,github,groq").split(",") if p.strip()]
    errors = []
    for p in order:
        if p in DEAD or (p == "github" and not GH_TOKEN) or (p == "groq" and not GROQ_KEY) or (p == "gemini" and not KEY):
            continue
        try:
            if p == "gemini":
                return _gemini(prompt, json_mode=json_mode, temperature=temperature)
            text = _openai_compat(p, prompt, json_mode, temperature)
            print(f"  (answered by {p})")
            return text, {}
        except RuntimeError as ex:
            errors.append(f"{p}: {str(ex)[:200]}")
            print(f"  {p} unavailable — trying the next AI. ({str(ex)[:120]})")
            DEAD.add(p)
    raise RuntimeError("All AIs failed: " + " | ".join(errors))


def resolve_model():
    """Use GEMINI_MODEL if it exists; otherwise pick the newest stable 'flash' text model available to this key.
    Google retires model names over time, so this keeps the pipeline working without edits."""
    global MODEL
    if MODEL != "auto":
        r = requests.get(f"{API}/{MODEL}", headers={"x-goog-api-key": KEY}, timeout=30)
        if not r.ok:
            print(f"Model {MODEL} unavailable ({r.status_code}); auto-selecting a current one.")
            MODEL = "auto"
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
    ordered = [m["name"].split("/")[-1] for m in sorted(cands, key=score, reverse=True)]
    best_major = score(max(cands, key=score))[1] // 1
    ordered = [n for n in ordered if (score({"name": n})[1] // 1) >= best_major - 0] or ordered  # same generation only
    FALLBACKS[:] = [m for m in ordered if m != MODEL] if MODEL != "auto" else ordered[1:]
    MODEL = ordered[0] if MODEL == "auto" or MODEL not in ordered else MODEL
    print("Using model:", MODEL, "| backups:", FALLBACKS[:4])


def gemini_json(prompt, temperature=0.6):
    def unwrap(x):
        # JSON-mode AIs must return an object, so lists often arrive as {"items": [...]} — unwrap them
        if isinstance(x, dict) and len(x) == 1 and isinstance(next(iter(x.values())), list):
            return next(iter(x.values()))
        return x
    for _ in range(3):
        text, _ = gemini(prompt, json_mode=True, temperature=temperature)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return unwrap(json.loads(text))
        except json.JSONDecodeError:
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            if m:
                try:
                    return unwrap(json.loads(m.group(0)))
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
        if not r.ok:
            break
        return [round(v, 5) for v in r.json()["embedding"]["values"]]
    print("  embedding unavailable — using title-overlap check only")
    return None


# ---------------- Topic selection ----------------
# Order every day:  1) best trending tech topic (explainer angle)  2) unused idea from the idea bank
# (your list + ideas the system invents itself)  3) invent brand-new ideas on the spot.
# The idea bank refills itself automatically, so it never runs out.
TREND_MIN_FIT = 8          # trending topic must score >= this (1-10) for fit with TechDcoded
AUTO_REFILL_BELOW = 40     # when fewer unused ideas remain, invent more
AUTO_REFILL_COUNT = 30
HT = "{https://trends.google.com/trending/rss}"

# Niche focus: TechDcoded decodes HOW TECHNOLOGY WORKS. Core categories are chosen ~80% of days;
# edge categories (business, careers, crime, philosophy...) ~20%, and only when the angle explains the tech.
EDGE_CATEGORIES = {
    "Tech Investigations", "Tech Experiments", "Tech Predictions", "Tech Psychology", "Tech Crime", "Tech History",
    "Tech Business", "Tech Careers", "Digital Society", "Tech Philosophy", "Forbidden Technology", "The Race",
    "Million-Dollar Mistakes", "Tech Black Market", "24 Hours Without", "Tech Survival", "Future Crimes", "Invisible Wars",
    "One Day As", "Can You Live Without", "AI vs Human", "AI vs AI", "Government Tech Controversies",
    "Corporate Tech Frauds", "Cybercrime Stories", "Cryptocurrency Scams", "Untold Tech Heroes", "Stories From the Future",
    "What Really Happened", "How Rich Could You Get", "What If You Were", "Dark Patterns Exposed", "Hidden Business Models",
    "Technology Horror", "Tech Layoffs & Careers", "AI & Workforce", "Psychology of Tech", "Tech & Society",
}
CORE_SHARE = 1.0   # 100% core technology topics
NICHE_MIN = 7   # topic must score >= 7/10 on "decodes how a technology works"
NICHE_RULE = ("NICHE RULE: TechDcoded explains HOW TECHNOLOGY WORKS — the engineering, science, systems and inner workings "
              "behind gadgets, apps, AI, internet, payments, vehicles, space and everyday tech. The reader must finish "
              "understanding a technology better. Business, careers, finance, crime or social topics only qualify when the "
              "article explains the technology behind them (e.g. 'How the Twitter Bitcoin hack actually worked' fits; "
              "'Why profitable companies lay off workers' does not).")


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
    for c in cands if isinstance(cands, list) else []:
        if not isinstance(c, dict):
            continue
        t = c.get("title", "").strip()
        if not t:
            continue
        nf = c.get("niche_fit")
        if isinstance(nf, (int, float)) and nf < NICHE_MIN:
            print(f"skip (off-niche {nf}/10):", t); continue
        if any(jaccard(t, x) > JAC_LIMIT for x in existing_titles):
            print("skip (title overlap):", t); continue
        vec = embed(f"{t}. {c.get('angle', '')}")
        top = max((cosine(vec, v) for v in emb.values()), default=0) if vec else 0
        if top > SIM_LIMIT:
            print(f"skip (similar {top:.2f}):", t); continue
        if (c.get("category") or cat) in EDGE_CATEGORIES:
            print("skip (edge category):", t); continue
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
Category: choose the closest from {json.dumps(sorted(set(categories) - EDGE_CATEGORIES))} (or a short new technology category if none fit).
fit: 1-10 for how well it suits TechDcoded AND how much lasting search interest it will have.
{NICHE_RULE}
niche_fit: 1-10 for how much the article decodes how a technology works (be strict).
Do NOT repeat these published articles: {json.dumps(existing[:60], ensure_ascii=False)}

Headlines:
{json.dumps(heads, ensure_ascii=False)}

Return JSON: [{{"title": "...", "primary_keyword": "...", "angle": "...", "category": "...", "fit": n,
"trend_context": "one line: what is trending and why, with the date if known", "idea": "short topic name", "niche_fit": n}}]"""
    try:
        cands = gemini_json(prompt, temperature=0.5)
    except Exception as ex:
        print("trend pick failed:", ex); return None
    cands = cands if isinstance(cands, list) else []
    cands = sorted([c for c in cands if isinstance(c, dict) and (c.get("fit") or 0) >= TREND_MIN_FIT], key=lambda c: -(c.get("fit") or 0))
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
to add up to 3 new technology categories: {json.dumps(sorted(set(cats) - EDGE_CATEGORIES))}
Each idea must be clearly different from every example and from these published articles:
{json.dumps([a["title"] for a in articles][:120], ensure_ascii=False)}
Prefer ideas with steady Google search demand. No politics, no medical or financial advice.
{NICHE_RULE}
ALL ideas must be in technology-explainer categories (how it works, gadgets, AI, internet, hardware,
fintech systems, space, vehicles, everyday tech science, comparisons, reverse engineering, cost of...).

Return JSON: [{{"category": "...", "idea": "..."}}]"""
    try:
        new = gemini_json(prompt, temperature=1.0)
    except Exception as ex:
        print("idea invention failed:", ex); return 0
    known = {i.lower() for _, i in topics}
    lines = []
    for x in new if isinstance(new, list) else []:
        if not isinstance(x, dict):
            continue
        c, i = str(x.get("category", "")).strip(), str(x.get("idea", "")).strip()
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
    fresh = [t for t in load_topics() if t[1] not in used and t[0] not in EDGE_CATEGORIES]
    if len(fresh) < AUTO_REFILL_BELOW:
        invent_ideas(articles)
        fresh = [t for t in load_topics() if t[1] not in used and t[0] not in EDGE_CATEGORIES]
    cat_count = {}
    for a in articles:
        cat_count[a.get("category", "")] = cat_count.get(a.get("category", ""), 0) + 1
    random.shuffle(fresh)
    fresh.sort(key=lambda t: cat_count.get(t[0], 0))  # least-covered categories first
    core = [t for t in fresh if t[0] not in EDGE_CATEGORIES]
    edge = [t for t in fresh if t[0] in EDGE_CATEGORIES]
    fresh = core   # edge categories are never used
    if not fresh:
        invent_ideas(articles)
        fresh = [t for t in load_topics() if t[1] not in used and t[0] not in EDGE_CATEGORIES]
    existing = [a["title"] for a in articles]
    for cat, idea in fresh[:6]:
        same_cat = [a["title"] for a in articles if a.get("category") == cat]
        prompt = f"""{STYLE}

Propose 5 distinct blog article titles for the TechDcoded category "{cat}" based on this idea: "{idea}".
Each title must target something people actually search on Google (a question or clear phrase), be specific,
40-65 characters, and promise real value. Prefer evergreen angles.
{NICHE_RULE}
If the idea is not about technology itself, find the technology angle inside it (how the systems/tools behind it work).
Do NOT overlap with these existing TechDcoded articles:
{json.dumps(same_cat[-40:] + existing[:40], ensure_ascii=False)}

niche_fit: 1-10 for how much the article decodes how a technology works (be strict).
Return JSON: [{{"title": "...", "primary_keyword": "...", "angle": "one line on what the article covers", "niche_fit": n}}]"""
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


WRITE_SPEC = """Reply in EXACTLY this plain-text format (keep every ### LABEL ### line; no JSON, no code fences):

### TITLE ###
SEO title, 40-65 characters, containing the primary keyword naturally
### META ###
Meta description, 140-160 characters, containing the primary keyword
### KEYWORD ###
primary keyword
### SECONDARY ###
5-8 related search phrases, comma separated
### TLDR ###
2-3 sentence direct answer to the title question
### TAKEAWAYS ###
- 4-5 short bullet takeaways, one per line
### BODY ###
The full article body in Markdown (rules below)
### FAQ ###
Q: a real question people search
A: 40-80 word answer
(5 pairs)
### IMAGE_ALT ###
short description of the topic for the featured image
### VISUALS ###
2-3 lines, each: heading text | photo or illustration | 2-4 word stock-photo search | one-sentence caption
(heading text = the exact ## heading the picture belongs under)
### END ###

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

VISUALS: use "photo" for real objects/places/people-at-work, "illustration" for abstract concepts. Write in English."""


def write(topic, notes, feedback=None, previous=None):
    extra = ""
    if feedback:
        extra = (f"\n\nAn editor reviewed your previous draft and found these problems — fix ALL of them:\n"
                 f"{json.dumps(feedback, ensure_ascii=False)}\n\nPrevious draft:\n{to_text(previous)[:14000]}")
    prompt = f"""{STYLE}

Write a complete, genuinely helpful, SEO-optimised article.
{NICHE_RULE}
Keep the focus on explaining the technology: how it works inside, step by step, with analogies a non-engineer understands.
Working title: "{topic['title']}"  |  Primary keyword: "{topic.get('primary_keyword', '')}"  |  Angle: {topic['angle']}
Category: {topic['category']}
{("This topic is trending right now: " + topic['trend_context'] + chr(10) + "Open with a short hook about why it is in the news, then deliver an evergreen explainer that stays useful for months. Do not write a news report.") if topic.get('source') == 'trending' else ''}

Research notes (your ONLY source of facts):
{notes[:9000]}
{extra}

{WRITE_SPEC}"""
    last = None
    for _ in range(3):
        text, _ = gemini(prompt, temperature=0.7)
        try:
            return parse_article(text)
        except RuntimeError as ex:
            last = ex
            print("  draft not in the expected format, asking again:", ex)
    raise RuntimeError(f"Could not get a complete article: {last}")


def mechanical_issues(a):
    """Returns (hard, soft, word_count). Hard problems block publishing; soft ones are requested in the
    rewrite but tolerated (and auto-fixed where possible) if the editor scores the article well."""
    hard, soft = [], []
    body = a.get("body_md", "")
    wc = len(re.findall(r"\w+", body))
    if wc < 900: hard.append(f"Body is only {wc} words; must be at least {MIN_WORDS + 200}.")
    elif wc < MIN_WORDS: soft.append(f"Body is {wc} words; aim for {MIN_WORDS + 200}+.")
    if re.search(r"as an ai (language )?model|\[insert|lorem ipsum|\bTODO\b", body, re.I):
        hard.append("Contains AI boilerplate/placeholder text.")
    if len(re.findall(r"^## ", body, re.M)) < 5: soft.append("Use at least 6 '## ' sections.")
    if len(a.get("faq", [])) < 4: soft.append("Include 5 FAQ items.")
    if not (30 <= len(a.get("title", "")) <= 70): soft.append("Title should be 40-65 characters.")
    if not (110 <= len(a.get("meta_description", "")) <= 170): soft.append("Meta description should be 140-160 characters.")
    nblocks = len(re.findall(r"^:::[ \t]*[a-zA-Z]+", body, re.M))
    if nblocks < 4: soft.append(f"Only {nblocks} visual blocks; include 5-8 (::: blocks).")
    if not re.search(r"^:::[ \t]*steps", body, re.M): soft.append("Add at least one :::steps process diagram.")
    return hard, soft, wc


def autofix(a):
    """Small, safe fixes so a good article is never rejected over formatting."""
    a["body_md"] = re.sub(r"^# .*\n?", "", a.get("body_md", ""), flags=re.M).strip()
    md = re.sub(r"\s+", " ", a.get("meta_description", "")).strip()
    if len(md) > 160:
        md = md[:157].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    if len(md) < 110 and a.get("tldr"):
        md = re.sub(r"\s+", " ", a["tldr"]).strip()
        md = md if len(md) <= 160 else md[:157].rsplit(" ", 1)[0] + "…"
    a["meta_description"] = md
    a["title"] = re.sub(r"\s+", " ", a.get("title", "")).strip().strip('"')
    return a


def review(article, notes):
    prompt = f"""You are a strict senior editor for a tech-explainer site that must rank on Google and never publish errors.
Review this article against the research notes. Score 1-10 each: accuracy (claims supported by notes, no invented numbers),
helpfulness (answers the title fully, specific, not generic), readability (clear for non-experts), originality (fresh insight,
not generic filler), safety (no harmful instructions). List concrete problems to fix.

Research notes:
{notes[:6000]}

Article:
{to_text(article)[:16000]}

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
        v = embed(f"{a['title']}. {a.get('meta_description', '')}")
        if v:
            emb[a["slug"]] = v
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
        art = autofix(art)
        hard, soft, wc = mechanical_issues(art)
        rev = review(art, notes)
        print(f"Review attempt {attempt + 1}:", {k: v for k, v in rev.items() if k != 'problems'}, "words:", wc)
        if hard or soft:
            print("  Format notes:", hard + soft)
        if not hard and passes(rev) and (not soft or attempt == 1):
            break
        if attempt == 1:
            sys.exit("Quality gate failed twice — skipping today. Problems: " + json.dumps(hard + rev.get("problems", []))[:1500])
        art = write(topic, notes, feedback=hard + soft + rev.get("problems", []), previous=art)

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
    if topic.get("vector"):
        emb[slug] = topic["vector"]
    save_embeddings(emb)
    print("Saved:", slug)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"slug={slug}\ntitle={record['title']}\n")


if __name__ == "__main__":
    main()
