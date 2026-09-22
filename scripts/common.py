"""Shared helpers for the TechDcoded article pipeline."""
import json, os, re, datetime, math, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "articles"
DATA = ROOT / "data"
SITE = "https://techdcoded.com"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

SOCIAL = [
    ("YouTube", "https://www.youtube.com/@techdcoded"),
    ("Instagram", "https://www.instagram.com/techdcoded"),
    ("Facebook", "https://www.facebook.com/techdcoded/"),
    ("X", "https://x.com/techdcoded"),
    ("LinkedIn", "https://www.linkedin.com/in/techdcoded"),
    ("WhatsApp", "https://whatsapp.com/channel/0029Vb8OFIr5vKA1diUdno3a"),
]


def now_ist():
    return datetime.datetime.now(IST)


def slugify(text, maxlen=70):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    if len(text) > maxlen:
        text = text[:maxlen].rsplit("-", 1)[0]
    return text.strip("-") or "article"


def load_topics():
    """Return list of (category, idea) from your list + auto-invented ideas."""
    out = []
    for fname in ("topics.txt", "ideas_auto.txt"):
        p = DATA / fname
        if p.exists():
            out += _parse_topics(p.read_text(encoding="utf-8"))
    return out


def _parse_topics(text):
    out, cat = [], "General"
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("## "):
            cat = line[3:].strip()
            continue
        out.append((cat, line))
    return out


def load_articles():
    arts = []
    for p in sorted(CONTENT.glob("*.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        a["_file"] = p.name
        arts.append(a)
    arts.sort(key=lambda a: (a["date"], a.get("time", "")), reverse=True)
    return arts


def load_embeddings():
    p = DATA / "embeddings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_embeddings(emb):
    (DATA / "embeddings.json").write_text(json.dumps(emb, separators=(",", ":")))


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)); db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


STOP = set("the a an of to in on for and or is are how what why does do can we you your with vs behind tech technology explained work works really inside".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def jaccard(a, b):
    A, B = words(a), words(b)
    return len(A & B) / len(A | B) if A and B else 0.0
