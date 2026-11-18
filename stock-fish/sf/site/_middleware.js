// Password gate for the private page site (Cloudflare Pages Functions; user decision 2026-11-18).
// Every request needs a cookie derived from the SITE_PASSWORD secret; without the secret the site stays closed.
const COOKIE = "lm_auth";
const MAX_AGE = 60 * 60 * 24 * 30;
const HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
  "x-robots-tag": "noindex, nofollow",
  "referrer-policy": "no-referrer",
};

async function token(secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode("longmai-site-v1"));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function same(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function escape(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function safePath(p) {
  return typeof p === "string" && p.startsWith("/") && !p.startsWith("//") && !p.startsWith("/\\") ? p : "/";
}

function login(message, status, back) {
  const body = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>龙脉</title><style>
body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f6f6f4;color:#222;display:flex;min-height:100vh;
margin:0;align-items:center;justify-content:center}form{background:#fff;padding:28px 24px;border-radius:12px;
box-shadow:0 2px 12px rgba(0,0,0,.08);width:min(320px,calc(100vw - 32px));box-sizing:border-box}
h1{font-size:20px;margin:0 0 16px}input{width:100%;box-sizing:border-box;font-size:16px;padding:10px;margin:0 0 12px;
border:1px solid #ccc;border-radius:8px}button{width:100%;font-size:16px;padding:10px;border:0;border-radius:8px;
background:#b03a2e;color:#fff}p{color:#b03a2e;margin:0 0 12px;font-size:14px}
@media (prefers-color-scheme:dark){body{background:#1b1b1b;color:#eee}form{background:#262626}
input{background:#1b1b1b;color:#eee;border-color:#444}}</style></head><body>
<form method="post" action="/__login"><h1>龙脉</h1>${message ? `<p>${escape(message)}</p>` : ""}
<input type="password" name="password" placeholder="密码" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="${escape(safePath(back))}"><button type="submit">进入</button></form></body></html>`;
  return new Response(body, { status, headers: HEADERS });
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const secret = env.SITE_PASSWORD;
  if (!secret || secret.length < 16) {
    return new Response("站点未设置密码，暂不开放。", { status: 503, headers: HEADERS });
  }
  const url = new URL(request.url);
  const good = await token(secret);

  if (url.pathname === "/__login" && request.method === "POST") {
    const form = await request.formData();
    const back = safePath(String(form.get("next") || "/"));
    if (same(String(form.get("password") || ""), secret)) {
      return new Response(null, {
        status: 303,
        headers: {
          location: back,
          "set-cookie": `${COOKIE}=${good}; Path=/; Max-Age=${MAX_AGE}; HttpOnly; Secure; SameSite=Lax`,
          "cache-control": "no-store",
        },
      });
    }
    return login("密码不对。", 401, back);
  }
  if (url.pathname === "/__logout") {
    return new Response(null, {
      status: 303,
      headers: { location: "/", "set-cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` },
    });
  }

  const cookies = Object.fromEntries((request.headers.get("cookie") || "").split(";").map((c) => {
    const i = c.indexOf("=");
    return i < 0 ? [c.trim(), ""] : [c.slice(0, i).trim(), c.slice(i + 1).trim()];
  }));
  if (cookies[COOKIE] && same(cookies[COOKIE], good)) {
    const response = await next();
    const out = new Response(response.body, response);
    out.headers.set("cache-control", "private, no-store");
    return out;
  }
  return login("", 401, url.pathname + url.search);
}
