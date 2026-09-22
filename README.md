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
