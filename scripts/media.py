"""Pictures for articles.

- Photos: free Pexels API (set PEXELS_API_KEY). Real, relevant, license-free photos with credit.
- Illustrations (optional): Gemini image model (set IMAGE_MODEL, e.g. gemini-2.5-flash-image; needs billing
  on your Google AI account). Brand-coloured, text-free concept art.
If neither is configured the article still gets designed diagrams, stat cards and callouts from blocks.py.
"""
import base64, io, json, os, random
import requests
from PIL import Image
from common import ROOT, DATA

IMG = ROOT / "assets" / "articles"
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "")
USED = DATA / "used_photos.json"
STYLE = ("Editorial 3D illustration for a technology explainer website. Dark navy background, glowing electric blue, "
         "cyan and violet accents, clean modern isometric look, soft lighting, high detail. Absolutely no text, "
         "letters, numbers, logos or watermarks. Subject: ")


def _save(im, base):
    IMG.mkdir(parents=True, exist_ok=True)
    im = im.convert("RGB")
    if im.width > 1200:
        im = im.resize((1200, round(im.height * 1200 / im.width)), Image.LANCZOS)
    im.save(IMG / f"{base}.jpg", quality=84, optimize=True, progressive=True)
    im.save(IMG / f"{base}.webp", quality=78, method=6)
    return im.width, im.height


def pexels_photo(query, used):
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "orientation": "landscape", "per_page": 15, "size": "large"}, timeout=30)
    r.raise_for_status()
    photos = [p for p in r.json().get("photos", []) if str(p["id"]) not in used]
    if not photos:
        return None
    p = photos[0] if len(photos) < 3 else random.choice(photos[:3])
    data = requests.get(p["src"].get("large2x") or p["src"]["large"], timeout=60).content
    return Image.open(io.BytesIO(data)), p


def ai_illustration(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGE_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": STYLE + prompt + ". Wide 16:9 composition."}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]}}
    r = requests.post(url, json=body, headers={"x-goog-api-key": GEMINI_KEY}, timeout=180)
    r.raise_for_status()
    for part in r.json()["candidates"][0]["content"]["parts"]:
        blob = part.get("inlineData") or part.get("inline_data")
        if blob:
            return Image.open(io.BytesIO(base64.b64decode(blob["data"])))
    return None


def get_visuals(visuals, slug):
    """visuals: [{after_heading, type: photo|illustration, query, prompt, caption, alt}] -> images list for the page."""
    used = set(json.loads(USED.read_text())) if USED.exists() else set()
    images = []
    for n, v in enumerate(visuals or [], 1):
        base = f"{slug}-{n}"
        try:
            got = None
            if v.get("type") == "illustration" and IMAGE_MODEL and GEMINI_KEY:
                im = ai_illustration(v.get("prompt") or v.get("query", ""))
                if im:
                    w, h = _save(im, base)
                    got = {"credit": "Illustration: TechDcoded"}
            if not got and PEXELS_KEY and v.get("query"):
                res = pexels_photo(v["query"], used)
                if res:
                    im, p = res
                    w, h = _save(im, base)
                    used.add(str(p["id"]))
                    got = {"credit": f"Photo: {p['photographer']} / Pexels", "credit_url": p["url"]}
            if got:
                images.append({"file": base, "w": w, "h": h, "after_heading": v.get("after_heading", ""),
                               "caption": v.get("caption", ""), "alt": v.get("alt") or v.get("caption", ""), **got})
        except Exception as ex:
            print("visual failed:", v.get("query") or v.get("prompt"), ex)
    USED.write_text(json.dumps(sorted(used)))
    return images
