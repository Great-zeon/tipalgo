# TIPALGO / SPORTYMILL — PRODUCTION CUSTOM DOMAIN CONNECTION GUIDE

This document provides step-by-step instructions to connect your custom domain (e.g. `tipalgo.com`, `sportymill.com`, or any subdomain) to your production deployment on Railway, Render, Cloudflare, or VPS.

---

## 1. Architectural Overview & SSL Termination

TipAlgo v21 / SportyMill is served by FastAPI (`uvicorn` ASGI server) behind an SSL termination proxy (Railway edge proxy or Cloudflare proxy).

When connecting a custom domain:
1. **Public Traffic** hits your DNS provider / CDN across port 443 with automated TLS/SSL.
2. **Reverse Proxy** forwards requests to TipAlgo on port `8000` (or `$PORT`).
3. **CORS & Origin Validation:** The server reads `ALLOWED_ORIGINS` to permit browser requests from your domain.

---

## 2. DNS Configuration (Step-by-Step)

### Option A: Railway Custom Domain Setup (Recommended)
1. In your **Railway Dashboard**, open your project service settings.
2. Navigate to **Settings** -> **Domains**.
3. Click **Custom Domain** and enter your domain:
   - For apex domain: `tipalgo.com` (or `sportymill.com`)
   - For subdomains: `www.tipalgo.com` or `app.tipalgo.com`
4. Railway will display the exact DNS records required:

| Type | Name / Host | Target / Value | TTL |
|------|-------------|----------------|-----|
| **CNAME** | `www` (or `app`) | `<your-service>.up.railway.app` | Auto / 300s |
| **ALIAS / ANAME** (or A record) | `@` (root domain) | Provided by Railway DNS edge | Auto / 300s |

Railway automatically provisions an SSL certificate via Let's Encrypt once DNS propagates.

### Option B: Cloudflare DNS & Proxy Setup
1. Point your domain nameservers to Cloudflare.
2. Under Cloudflare **DNS Records**:
   - Add a `CNAME` for `@` pointing to `<your-service>.up.railway.app` (Proxy status: **Proxied**).
   - Add a `CNAME` for `www` pointing to `@` (Proxy status: **Proxied**).
3. Under Cloudflare **SSL/TLS**:
   - Set encryption mode to **Full (Strict)**.
   - Enable **Always Use HTTPS**.

---

## 3. Environment Variable Configuration

In your production environment variables (Railway Variables tab or `.env` file):

```ini
# Production Environment
ENVIRONMENT=production
DEBUG=False

# Allowed Origins (Comma-separated custom domains)
ALLOWED_ORIGINS=https://tipalgo.com,https://www.tipalgo.com,https://sportymill.com,https://www.sportymill.com

# Server Bind Configuration
PORT=8000
HOST=0.0.0.0

# Base API & Ingestion
DATABASE_PATH=/data/tipalgo.db
```

FastAPI automatically parses `ALLOWED_ORIGINS` to allow cross-origin requests, fonts, WebSocket, and session tokens from your domain.

---

## 4. Verification Checklist Before Public Launch

- [x] Custom domain DNS propagation verified (`dig +short tipalgo.com`)
- [x] HTTPS / SSL lock active with zero mixed-content warnings
- [x] Favicon suite loaded from root (`/favicon.ico`, `/favicon.svg`, `/apple-touch-icon.png`)
- [x] Privacy Policy live at `/privacy`
- [x] Terms and Conditions live at `/terms`
- [x] Custom 404 page handles invalid routes (`/404`)
- [x] GZip compression active (transfers <100KB for app.js and style.css)
- [x] Static assets served with caching headers (`Cache-Control: public, max-age=31536000`)
- [x] Zero "Made with AI" tags or sloppy gradients in UI
- [x] Mobile menu operational with zero horizontal overflow

