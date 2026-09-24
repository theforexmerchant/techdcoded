"""Share new articles on other platforms automatically.

Every platform is optional: it runs only when its GitHub secrets are set.
Each post is a short version (intro + key takeaways) that links back to the full article,
and blog copies declare techdcoded.com as the original (canonical) so Google never sees duplicates.
Progress is saved in data/syndicated.json so nothing is posted twice and failures are retried next run.
"""
import json, os, re, sys, time
import markdown
import requests
from common import DATA, SITE, load_articles, now_ist

LOG = DATA / "syndicated.json"
MAX_AGE_DAYS = 14          # only share recent articles
PER_RUN = 2                # max articles per platform per run (keeps it natural, never floods)
env = os.environ.get
FB_VER = env("FB_GRAPH_VERSION") or "v23.0"


# ---------------- helpers ----------------
def url_of(a):
    return f"{SITE}/articles/{a['slug']}/"


def cover(a):
    return f"{SITE}/assets/articles/{a['slug']}.jpg"


def brand(t):
    return (t or "").replace("TechDcoded", "TechDCoded")


def as_list(v):
    if isinstance(v, list):
        return v
    try:
        import ast
        return ast.literal_eval(v) if v else []
    except Exception:
        return []


def tags(a, n=4):
    words = re.findall(r"[a-z0-9]+", (a.get("category", "") + " technology explained").lower())
    out = []
    for w in words:
        if w not in ("and",) and w not in out:
            out.append(w)
    return out[:n]


def intro(a):
    body = re.split(r"\n##\s", "\n" + brand(a["body_md"]), maxsplit=1)[0]
    body = re.sub(r":::.*?:::", "", body, flags=re.S)        # drop visual blocks
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"^#.*$", "", body, flags=re.M)
    return body.strip()


def excerpt_md(a):
    k = "".join(f"- {brand(x)}\n" for x in as_list(a.get("key_takeaways"))[:5])
    return (f"![{a.get('image_alt', a['title'])}]({cover(a)})\n\n{intro(a)}\n\n"
            + (f"## Key takeaways\n\n{k}\n" if k else "")
            + f"**👉 Read the full explainer with diagrams and FAQs on TechDCoded:** [{brand(a['title'])}]({url_of(a)})\n\n"
            + f"*Originally published at [techdcoded.com]({url_of(a)}).*\n")


def short(a, limit):
    t = brand(a["title"])
    d = brand(a.get("meta_description", ""))
    base = f"{t}\n\n{d}"
    return base if len(base) <= limit else t[:limit]


def ok(r):
    if r.status_code >= 300:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return r


# ---------------- platforms ----------------
def devto(a):
    r = ok(requests.post("https://dev.to/api/articles", timeout=60, headers={"api-key": env("DEVTO_API_KEY")}, json={
        "article": {"title": brand(a["title"]), "body_markdown": excerpt_md(a), "published": True,
                    "canonical_url": url_of(a), "main_image": cover(a),
                    "description": brand(a.get("meta_description", ""))[:250], "tags": tags(a)}}))
    return r.json().get("url", "ok")


def _gql(query, variables=None):
    """Hashnode GraphQL call with clear errors (their gateway sometimes replies with HTML, not JSON)."""
    r = requests.post("https://gql.hashnode.com", timeout=60,
                      json={"query": query, "variables": variables or {}},
                      headers={"Authorization": (env("HASHNODE_TOKEN") or "").strip(),
                               "Content-Type": "application/json", "Accept": "application/json"})
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"hashnode replied with non-JSON ({r.status_code}): {r.text[:200]}")
    if j.get("errors"):
        raise RuntimeError(f"hashnode error ({r.status_code}): {str(j['errors'])[:250]}")
    if r.status_code >= 300:
        raise RuntimeError(f"hashnode {r.status_code}: {r.text[:200]}")
    return j["data"]


def hashnode_publication_id():
    """Use the given ID, or look it up from the blog address so a wrong/missing ID isn't fatal."""
    pid = (env("HASHNODE_PUBLICATION_ID") or "").strip()
    if re.fullmatch(r"[0-9a-f]{24}", pid or ""):
        return pid
    host = (env("HASHNODE_HOST") or "techdcoded.hashnode.dev").strip().replace("https://", "").strip("/")
    data = _gql("query P($host: String!) { publication(host: $host) { id title } }", {"host": host})
    pub = (data or {}).get("publication")
    if not pub:
        raise RuntimeError(f"no Hashnode blog found at {host} — check HASHNODE_HOST")
    print(f"  hashnode publication resolved: {pub.get('title')} ({pub['id']})")
    return pub["id"]


def hashnode(a):
    q = "mutation P($input: PublishPostInput!) { publishPost(input: $input) { post { url } } }"
    inp = {"title": brand(a["title"]), "contentMarkdown": excerpt_md(a), "publicationId": hashnode_publication_id(),
           "originalArticleURL": url_of(a), "coverImageOptions": {"coverImageURL": cover(a)},
           "tags": [{"slug": t, "name": t.title()} for t in tags(a)]}
    return _gql(q, {"input": inp})["publishPost"]["post"]["url"]


def blogger(a):
    tok = google_token("BLOGGER")
    html = markdown.markdown(excerpt_md(a))
    r = ok(requests.post(f"https://www.googleapis.com/blogger/v3/blogs/{env('BLOGGER_BLOG_ID')}/posts/", timeout=60,
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"title": brand(a["title"]), "content": html, "labels": [a.get("category", "Technology")]}))
    return r.json().get("url", "ok")


def google_token(prefix):
    return ok(requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": env(prefix + "_CLIENT_ID"), "client_secret": env(prefix + "_CLIENT_SECRET"),
        "refresh_token": env(prefix + "_REFRESH_TOKEN"), "grant_type": "refresh_token"})).json()["access_token"]


def gbp(a):
    """Google Business Profile 'What's new' post (needs Google's approval for API access)."""
    tok = google_token("GBP")
    summary = f"{brand(a['title'])}\n\n{brand(a.get('tldr') or a.get('meta_description', ''))}"
    body = {"languageCode": "en-IN", "summary": summary[:1490], "topicType": "STANDARD",
            "callToAction": {"actionType": "LEARN_MORE", "url": url_of(a)},
            "media": [{"mediaFormat": "PHOTO", "sourceUrl": cover(a)}]}
    r = ok(requests.post(f"https://mybusiness.googleapis.com/v4/accounts/{env('GBP_ACCOUNT_ID')}"
                         f"/locations/{env('GBP_LOCATION_ID')}/localPosts", timeout=60,
                         headers={"Authorization": f"Bearer {tok}"}, json=body))
    return r.json().get("searchUrl", "ok")


def tumblr(a):
    from requests_oauthlib import OAuth1
    auth = OAuth1(env("TUMBLR_CONSUMER_KEY"), env("TUMBLR_CONSUMER_SECRET"), env("TUMBLR_TOKEN"), env("TUMBLR_TOKEN_SECRET"))
    body = {"state": "published", "tags": ",".join(tags(a, 6) + ["techdcoded"]), "content": [
        {"type": "image", "media": [{"type": "image/jpeg", "url": cover(a), "width": 1200, "height": 630}],
         "alt_text": a.get("image_alt", a["title"])},
        {"type": "text", "subtype": "heading1", "text": brand(a["title"])},
        {"type": "text", "text": brand(a.get("meta_description", ""))},
        {"type": "link", "url": url_of(a), "title": brand(a["title"]), "description": "Read the full explainer on TechDCoded"}]}
    r = ok(requests.post(f"https://api.tumblr.com/v2/blog/{env('TUMBLR_BLOG')}/posts", auth=auth, json=body, timeout=60))
    return f"https://{env('TUMBLR_BLOG')}/post/{r.json().get('response', {}).get('id', '')}"


def bluesky(a):
    pds = "https://bsky.social/xrpc"
    s = ok(requests.post(f"{pds}/com.atproto.server.createSession", timeout=30,
                         json={"identifier": env("BLUESKY_HANDLE"), "password": env("BLUESKY_APP_PASSWORD")})).json()
    h = {"Authorization": f"Bearer {s['accessJwt']}"}
    ext = {"uri": url_of(a), "title": brand(a["title"]), "description": brand(a.get("meta_description", ""))[:300]}
    try:
        img = requests.get(cover(a), timeout=30).content
        if len(img) < 950_000:
            ext["thumb"] = ok(requests.post(f"{pds}/com.atproto.repo.uploadBlob", data=img, timeout=60,
                                            headers={**h, "Content-Type": "image/jpeg"})).json()["blob"]
    except Exception as ex:
        print("  bluesky thumb skipped:", ex)
    text = short(a, 280)
    rec = {"$type": "app.bsky.feed.post", "text": text, "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "embed": {"$type": "app.bsky.embed.external", "external": ext}}
    r = ok(requests.post(f"{pds}/com.atproto.repo.createRecord", headers=h, timeout=30,
                         json={"repo": s["did"], "collection": "app.bsky.feed.post", "record": rec}))
    return f"https://bsky.app/profile/{env('BLUESKY_HANDLE')}/post/{r.json()['uri'].rsplit('/', 1)[-1]}"


def mastodon(a):
    tg = " ".join("#" + t.capitalize() for t in tags(a, 3))
    text = f"{short(a, 380)}\n\n{url_of(a)}\n\n{tg} #TechDCoded"
    r = ok(requests.post(f"{env('MASTODON_INSTANCE').rstrip('/')}/api/v1/statuses", timeout=30,
                         headers={"Authorization": f"Bearer {env('MASTODON_TOKEN')}"}, data={"status": text}))
    return r.json().get("url", "ok")


def telegram(a):
    import html as H
    cap = (f"<b>{H.escape(brand(a['title']))}</b>\n\n{H.escape(brand(a.get('tldr') or a.get('meta_description', ''))[:700])}"
           f"\n\n👉 <a href=\"{url_of(a)}\">Read the full article</a>")
    r = ok(requests.post(f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/sendPhoto", timeout=60, json={
        "chat_id": env("TELEGRAM_CHAT_ID"), "photo": cover(a), "caption": cap[:1024], "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "📖 Read article", "url": url_of(a)}]]}}))
    return "ok"


def facebook(a):
    msg = f"{brand(a['title'])}\n\n{brand(a.get('tldr') or a.get('meta_description', ''))}\n\nRead more 👇"
    r = ok(requests.post(f"https://graph.facebook.com/{FB_VER}/{env('FB_PAGE_ID')}/feed", timeout=60,
                         data={"message": msg, "link": url_of(a), "access_token": env("FB_PAGE_TOKEN")}))
    return f"https://facebook.com/{r.json().get('id', '')}"


PLATFORMS = {
    "devto": (devto, ["DEVTO_API_KEY"]),
    "hashnode": (hashnode, ["HASHNODE_TOKEN"]),   # publication id is looked up if not supplied
    "blogger": (blogger, ["BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN", "BLOGGER_BLOG_ID"]),
    "tumblr": (tumblr, ["TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET", "TUMBLR_TOKEN", "TUMBLR_TOKEN_SECRET", "TUMBLR_BLOG"]),
    "bluesky": (bluesky, ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"]),
    "mastodon": (mastodon, ["MASTODON_INSTANCE", "MASTODON_TOKEN"]),
    "telegram": (telegram, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    "facebook": (facebook, ["FB_PAGE_ID", "FB_PAGE_TOKEN"]),
    "google_business": (gbp, ["GBP_CLIENT_ID", "GBP_CLIENT_SECRET", "GBP_REFRESH_TOKEN", "GBP_ACCOUNT_ID", "GBP_LOCATION_ID"]),
}


def wait_live(a, minutes=6):
    """Link previews break if we post before Cloudflare has deployed the page."""
    end = time.time() + minutes * 60
    while time.time() < end:
        try:
            r = requests.get(url_of(a), timeout=20)
            if r.ok and a["slug"] in r.text:
                return True
        except requests.RequestException:
            pass
        time.sleep(20)
    return False


def main():
    active = {k: v for k, v in PLATFORMS.items() if all(env(s) for s in v[1])}
    if not active:
        print("No sharing platforms configured yet — add their secrets to turn them on.")
        return
    print("Sharing to:", ", ".join(active))
    log = json.loads(LOG.read_text()) if LOG.exists() else {}
    today = now_ist().date()
    recent = [a for a in load_articles()
              if (today - __import__("datetime").date.fromisoformat(a["date"])).days <= MAX_AGE_DAYS]
    live, summary = {}, []
    for name, (fn, _) in active.items():
        todo = [a for a in recent if name not in log.get(a["slug"], {})][:PER_RUN]
        for a in todo:
            if a["slug"] not in live:
                live[a["slug"]] = wait_live(a)
            if not live[a["slug"]]:
                print(f"  {a['slug']} not live yet — will retry next run"); continue
            try:
                res = fn(a)
                log.setdefault(a["slug"], {})[name] = res
                print(f"  ✓ {name}: {res}")
                summary.append(f"- ✓ {name}: {res}")
            except Exception as ex:
                print(f"  ✗ {name} failed (will retry next run): {ex}")
                summary.append(f"- ✗ {name}: {str(ex)[:200]}")
            time.sleep(3)
    LOG.write_text(json.dumps(log, indent=1))
    if env("GITHUB_STEP_SUMMARY") and summary:
        with open(env("GITHUB_STEP_SUMMARY"), "a") as f:
            f.write("### Shared on other platforms\n" + "\n".join(summary) + "\n")


if __name__ == "__main__":
    main()
