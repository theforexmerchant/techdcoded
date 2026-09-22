// Cloudflare Pages Function: GET /api/latest-video
// Returns the newest upload from the TechDcoded YouTube channel as JSON.
// Set your channel ID in Cloudflare Pages > Settings > Environment variables as YT_CHANNEL_ID
// (or replace the fallback below). Find it at youtube.com/account_advanced (starts with "UC").
const FALLBACK_CHANNEL_ID = "UCxxxxxxxxxxxxxxxxxxxxxx";

export async function onRequest({ env }) {
  const id = env.YT_CHANNEL_ID || FALLBACK_CHANNEL_ID;
  const headers = { "content-type": "application/json", "cache-control": "public, max-age=900" };
  try {
    const r = await fetch(`https://www.youtube.com/feeds/videos.xml?channel_id=${id}`, {
      cf: { cacheTtl: 900, cacheEverything: true },
    });
    if (!r.ok) throw new Error("feed " + r.status);
    const xml = await r.text();
    const entry = xml.split("<entry>")[1];
    if (!entry) throw new Error("no videos");
    const pick = (re) => (entry.match(re) || [])[1] || "";
    const videoId = pick(/<yt:videoId>([^<]+)<\/yt:videoId>/);
    const title = pick(/<title>([^<]+)<\/title>/)
      .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    const published = pick(/<published>([^<]+)<\/published>/);
    return new Response(JSON.stringify({ videoId, title, published, thumb: `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg` }), { headers });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e.message || e) }), { status: 502, headers: { ...headers, "cache-control": "no-store" } });
  }
}
