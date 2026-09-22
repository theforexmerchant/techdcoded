# techdcoded.com

Static one-page site. No build step.

## Deploy (GitHub + Cloudflare Pages)
1. Create a GitHub repo `techdcoded-site`, push these files to `main`:
   ```
   git init && git add . && git commit -m "Initial site"
   git branch -M main
   git remote add origin https://github.com/<you>/techdcoded-site.git
   git push -u origin main
   ```
2. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick the repo.
3. Build settings: Framework preset **None**, build command **empty**, output directory **/**. Deploy.
4. Project → Custom domains → add `techdcoded.com` and `www.techdcoded.com` (auto DNS if the domain is on Cloudflare).
5. Every `git push` redeploys automatically.

## Edit before going live
- Social links are live (YouTube, Instagram, Facebook, TikTok, Threads, Pinterest, Twitch, LinkedIn, X). Update both the cards and the JSON-LD `sameAs` list if any change.
- Google link is a Maps search placeholder: replace with your GBP share link (g.page/r/...)
- Email `business@techdcoded.com`
- Logo: assets/logo.png (transparent, top of page), favicon + apple-touch-icon + logo-mark.png from the new logo
- Banner: assets/banner.jpg (bottom strip of the original banner cropped off; the social box replaces it)

## Latest YouTube video (auto-updating)
- `functions/api/latest-video.js` is a Cloudflare Pages Function — it deploys automatically with the site.
- Cloudflare Pages → your project → Settings → Environment variables → add `YT_CHANNEL_ID` = your channel ID (starts with `UC`, find it at youtube.com/account_advanced). Redeploy.
- Refreshes every 15 min. Clicking the card plays the video in a popup on the site.
- Opening index.html locally shows the fallback card (the function only runs on Cloudflare).

## WhatsApp channel
- Linked to https://whatsapp.com/channel/0029Vb8OFIr5vKA1diUdno3a

## SEO checklist after going live
1. Google Search Console (search.google.com/search-console) → Add property → Domain → `techdcoded.com`.
   Verify with the TXT record — if the domain is on Cloudflare, Search Console offers one-click verification.
2. Sitemaps → submit `https://techdcoded.com/sitemap.xml`. Then URL Inspection → `https://techdcoded.com/` → Request indexing.
3. Bing Webmaster Tools → Import from Google Search Console (1 click).
4. Create/verify your Google Business Profile and swap its share link into the Google card + JSON-LD `sameAs`.
5. Put `https://techdcoded.com` in every social bio and your YouTube channel "Links" section — these backlinks matter most for ranking the brand name.
6. Cloudflare → Speed → enable Brotli + Auto Minify; Cloudflare Web Analytics for traffic.
7. When you change the page, update `<lastmod>` in sitemap.xml.

## Daily automatic articles (100% hands-off)
Every day at 07:00 IST, GitHub Actions (`.github/workflows/daily-article.yml`) runs on GitHub's servers:
1. `scripts/generate.py` chooses today's topic in this order:
   - **Trending first:** reads Google Trends India, Hacker News and a live Google Search of the last 48 hours of tech news, then picks the best-fitting subject (score ≥ 8/10) and writes it as a lasting *explainer*, not a news report.
   - **Idea bank:** if nothing strong is trending, an unused idea from `data/topics.txt` (your list) or `data/ideas_auto.txt` (ideas the system invents itself), least-covered category first.
   - **Never runs out:** when fewer than 40 unused ideas remain, it invents 30 new ones in the same style and saves them to `data/ideas_auto.txt`.
   - Every candidate is rejected if too similar to anything already published (title overlap + AI embeddings in `data/embeddings.json`).
2. Researches with Gemini + live Google Search, writes a 1,300–1,800 word article (quick answer, takeaways, sections, table, FAQ, sources).
3. Quality gate: an AI editor scores accuracy, helpfulness, readability, originality and safety. It gets one rewrite; if it still fails, nothing is published that day and GitHub emails you.
4. `scripts/build.py` builds `/articles/<slug>/`, the featured image, `/articles/` list, `sitemap.xml`, `feed.xml`, and the "Latest articles" box on the home page.
5. Commits → Cloudflare deploys → `scripts/indexnow.py` pings Bing/IndexNow.

### Visual, colourful articles
Every article is built with designed components, not just text: numbered colour-coded sections, flow diagrams (`:::steps`),
big-number stat cards, "Did you know?" / tip / warning callouts, myth-vs-fact cards, side-by-side comparisons, timelines,
key-term boxes, pull quotes, a reading progress bar and scroll animations (see `scripts/blocks.py`).
Pictures (optional but recommended):
- **Photos — free:** create a free Pexels API key at https://www.pexels.com/api/ and add it as repository secret `PEXELS_API_KEY`.
  Each article then gets 2–3 relevant photos with captions and credit, and the first photo becomes the featured image.
- **AI illustrations — paid (~₹3 per image):** enable billing on your Google AI Studio account and add repository *variable*
  `IMAGE_MODEL` = `gemini-2.5-flash-image`. Abstract concepts then get custom brand-coloured illustrations.

### One-time setup
1. Get a free Gemini API key: https://aistudio.google.com → Get API key.
2. GitHub repo → Settings → Secrets and variables → Actions → New repository secret → Name `GEMINI_API_KEY`, paste the key.
3. GitHub repo → Actions tab → enable workflows if asked → "Daily article" → Run workflow (to test immediately).

### Controls
- Add/remove ideas: edit `data/topics.txt` (`## Category` then one idea per line).
- Pause: Actions → Daily article → ⋯ → Disable workflow.
- Remove an article: delete its file in `content/articles/` and run the workflow (or wait for the next day's build).
- Turn trending off (idea bank only): Settings → Secrets and variables → Actions → Variables → `TRENDING` = `off`.
- Change model: Settings → Secrets and variables → Actions → Variables → `GEMINI_MODEL`.
- Change time: edit the cron line in the workflow (UTC; `30 1 * * *` = 07:00 IST).
