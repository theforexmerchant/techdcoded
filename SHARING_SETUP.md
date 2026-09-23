# Auto-sharing setup (one time)

After this, every new article is shared automatically. Do the platforms in any order; each one starts working as soon as its secrets are added. Skip any you don't want.

**Where to paste the keys:** GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**. Use the exact names in `CAPITALS`.

---

## 1. Telegram (5 min, easiest)
1. In Telegram, create a channel called **TechDCoded** (public).
2. Open **@BotFather** → send `/newbot` → give it a name → it replies with a token → secret **`TELEGRAM_BOT_TOKEN`**.
3. Add your new bot to the channel as an **Admin**.
4. Secret **`TELEGRAM_CHAT_ID`** = `@your_channel_username` (e.g. `@techdcoded`).

## 2. Dev.to (3 min)
1. Sign up at dev.to → **Settings → Extensions → DEV Community API Keys** → Generate.
2. Secret **`DEVTO_API_KEY`**.

## 3. Hashnode (5 min)
1. Sign up at hashnode.com and create a blog.
2. **Account settings → Developer → Generate new token** → secret **`HASHNODE_TOKEN`**.
3. Open your blog **Dashboard**; the URL looks like `hashnode.com/<ID>/dashboard` → copy the ID → secret **`HASHNODE_PUBLICATION_ID`**.

## 4. Bluesky (3 min)
1. Sign up at bsky.app.
2. **Settings → Privacy and security → App passwords → Add** → secret **`BLUESKY_APP_PASSWORD`**.
3. Secret **`BLUESKY_HANDLE`** = your handle, e.g. `techdcoded.bsky.social`.

## 5. Mastodon (3 min)
1. Sign up on a tech server, e.g. **mastodon.social** or **fosstodon.org**.
2. **Preferences → Development → New application** → name it TechDCoded → tick `write:statuses` → Submit → open it → copy **Your access token** → secret **`MASTODON_TOKEN`**.
3. Secret **`MASTODON_INSTANCE`** = `https://mastodon.social` (your server's address).

## 6. Tumblr (5 min)
1. Sign up at tumblr.com and create a blog (e.g. techdcoded).
2. Go to **tumblr.com/oauth/apps → Register application** (any website/callback: `https://techdcoded.com`).
3. Copy **OAuth Consumer Key** → **`TUMBLR_CONSUMER_KEY`**, **Secret Key** → **`TUMBLR_CONSUMER_SECRET`**.
4. Click **Explore API** next to your app → Allow → it shows **Token** and **Token Secret** → **`TUMBLR_TOKEN`**, **`TUMBLR_TOKEN_SECRET`**.
5. **`TUMBLR_BLOG`** = `techdcoded.tumblr.com`.

## 7. Facebook Page (10 min)
1. Go to **developers.facebook.com → My Apps → Create app** (type: Business). Note the **App ID** and **App Secret** (App settings → Basic).
2. Open **Graph API Explorer**, choose your app, click **Get User Access Token**, tick `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` → Generate → copy the token.
3. Paste this in the browser (fill the 3 values) to get a long-lived token:
   `https://graph.facebook.com/v23.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_FROM_STEP_2`
4. Then open: `https://graph.facebook.com/v23.0/me/accounts?access_token=LONG_TOKEN_FROM_STEP_3`
   Find your TechDCoded page → its **access_token** → **`FB_PAGE_TOKEN`** (this one never expires), its **id** → **`FB_PAGE_ID`**.

## 8. Blogger (15 min, the longest)
1. Create a blog at blogger.com (e.g. techdcoded.blogspot.com). In the dashboard URL, copy the number after `blogID=` → **`BLOGGER_BLOG_ID`**.
2. Go to **console.cloud.google.com** → create a project → **APIs & Services → Library** → enable **Blogger API v3**.
3. **OAuth consent screen** → External → fill app name + your email → **Publish app** ("In production", so the key never expires).
4. **Credentials → Create credentials → OAuth client ID** → type **Web application** → Authorized redirect URI: `https://developers.google.com/oauthplayground` → Create. Copy **Client ID** → **`BLOGGER_CLIENT_ID`**, **Client secret** → **`BLOGGER_CLIENT_SECRET`**.
5. Open **developers.google.com/oauthplayground** → gear icon → tick **Use your own OAuth credentials** → paste the ID and secret.
6. In the left box type `https://www.googleapis.com/auth/blogger` → **Authorize APIs** → sign in (click *Advanced → Go to app* if warned) → **Exchange authorization code for tokens** → copy the **Refresh token** → **`BLOGGER_REFRESH_TOKEN`**.

## 9. Google Business Profile (needs Google's approval first)
Your profile must already be verified. Google does not let software post until it approves your project.

1. **Apply first:** search "Google Business Profile API access request form", fill it in with your Google Cloud **project number** and your site, and wait for the approval email (usually 1–3 weeks). Without this every call fails with "permission denied" — that is expected until approval arrives.
2. In the same Google Cloud project as Blogger (or a new one), enable these APIs: **My Business Account Management API**, **My Business Business Information API** and **Google My Business API**.
3. Create an **OAuth client ID** (Web application) with redirect URI `https://developers.google.com/oauthplayground`, and publish the consent screen ("In production"). Copy them into **`GBP_CLIENT_ID`** and **`GBP_CLIENT_SECRET`**.
4. In **developers.google.com/oauthplayground** → gear → use your own credentials → scope `https://www.googleapis.com/auth/business.manage` → Authorize → Exchange → copy the **Refresh token** → **`GBP_REFRESH_TOKEN`**. Keep the playground tab open.
5. Still in the playground, call `GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts` → copy the number after `accounts/` → **`GBP_ACCOUNT_ID`**.
6. Then call `GET https://mybusinessbusinessinformation.googleapis.com/v1/accounts/ACCOUNT_ID/locations?readMask=name,title` → copy the number after `locations/` → **`GBP_LOCATION_ID`**.

---

## Test it
**Actions → Daily article → Run workflow** (force unticked). Open the run → the step **Share on other platforms** shows ✓ or ✗ for each platform. Failed ones retry automatically in the next run.

## Not included (and why)
- **Threads, LinkedIn, Pinterest, Instagram**: their keys expire every 1–2 months, so they can't stay hands-free.
- **Quora, Reddit, Medium, WhatsApp channel**: no automatic posting allowed; accounts get banned.
