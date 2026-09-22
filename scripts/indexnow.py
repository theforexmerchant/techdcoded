"""Ping IndexNow (Bing, Yandex, Seznam, Naver...) so new pages are indexed within hours."""
import sys, requests
KEY = "7c38902813c9b99e6f9b194f4dec3a16"
HOST = "techdcoded.com"
urls = [u for u in sys.argv[1:] if u]
if urls:
    r = requests.post("https://api.indexnow.org/indexnow", json={
        "host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls}, timeout=30)
    print("IndexNow:", r.status_code)
