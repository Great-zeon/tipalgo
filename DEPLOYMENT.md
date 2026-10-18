# TIPALGO v21 — PRODUCTION DEPLOYMENT & OPERATION MANUAL

This guide details how to deploy the TipAlgo Football Prediction Platform on **Railway** (or any containerized PaaS/VPS) connected to a **custom domain**, maintaining SQLite data persistence and zero-downtime health monitoring.

---

## 1. Hosting Architecture Overview

TipAlgo v21 is an asynchronous Python application built on **FastAPI**, backed by a stateful SQLite database (`tipalgo.db` with WAL mode) and a Scikit-learn model ([`screener_model.pkl`](file:///Users/macbookpro/.gemini/antigravity-ide/scratch/predict/screener_model.pkl)).

### Architectural Requirements:
* **Single Process / Instance:** Because SQLite uses write-ahead logging (WAL) for concurrency, the application runs as a **single backend instance** with internal thread pooling (`ThreadPoolExecutor(20)`) to avoid multi-instance SQLite lock contention.
* **Persistent Disk Volume:** SQLite requires a persistent disk mount (e.g. `/data`) so data is never lost during deploys or restarts.
* **Static Frontend:** Served directly from [`static/`](file:///Users/macbookpro/.gemini/antigravity-ide/scratch/predict/static/) by FastAPI.

---

## 2. Deploying to Railway (Step-by-Step)

### Step 1: Create a Railway Project
1. Log in to your [Railway Dashboard](https://railway.com).
2. Click **New Project** -> **Deploy from GitHub repo**.
3. Select the repository containing TipAlgo.

### Step 2: Attach a Persistent Volume
1. In your Railway service dashboard, click on your service.
2. Navigate to **Volumes** (or click **+ New** -> **Volume**).
3. Mount path: `/data`
4. Set size: `5 GB` (or larger depending on match archive requirements).

### Step 3: Configure Environment Variables
In the **Variables** tab of your service, configure:

```ini
ENVIRONMENT=production
DEBUG=False
PORT=8000
HOST=0.0.0.0
DATABASE_PATH=/data/tipalgo.db
SECRET_KEY=<generate-a-64-character-random-hex-string>
ADMIN_TOKEN=<generate-a-random-token-for-admin-endpoints>
FOOTBALL_API_KEY=<your-api-sports-key>
FOOTBALL_API_BASE=https://v3.football.api-sports.io
PAYSTACK_SECRET_KEY=<your-live-paystack-secret-key>
PAYSTACK_PUBLIC_KEY=<your-live-paystack-public-key>
PRO_PRICE=20000
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

### Step 4: Seed Initial Data on the Persistent Volume (First Deploy Only)
Before the first startup, seed the SQLite database onto the persistent disk volume `/data/tipalgo.db`:
* In Railway, open the **Service Terminal** or CLI:
  ```bash
  # Copy repository starter database to the persistent mount
  cp tipalgo.db /data/tipalgo.db
  # Or run the seed script to build a fresh dataset
  python3 seed.py
  ```

### Step 5: Deploy & Automatic Healthcheck
Railway automatically reads [`railway.json`](file:///Users/macbookpro/.gemini/antigravity-ide/scratch/predict/railway.json) and [`Procfile`](file:///Users/macbookpro/.gemini/antigravity-ide/scratch/predict/Procfile):
* **Start Command:** `python3 run.py`
* **Healthcheck Path:** `GET /health`
* When `/health` returns HTTP 200 with `"status": "ok"` and `"database": "connected"`, Railway routes live traffic to the new container.

---

## 3. Custom Domain Configuration (e.g. `tipalgo.com`)

1. In Railway, open the **Settings** tab of your service -> **Custom Domains**.
2. Click **Add Custom Domain** and enter:
   * `tipalgo.com` (Root domain)
   * `www.tipalgo.com` (Subdomain)
3. In your DNS provider (e.g. Cloudflare, Namecheap, GoDaddy):
   * **Root Domain:** Add an **A Record** pointing `@` to the IP address provided by Railway.
   * **WWW Subdomain:** Add a **CNAME Record** pointing `www` to your Railway service domain (e.g. `your-service.up.railway.app`).
4. Railway automatically provisions a free, auto-renewing SSL certificate via Let's Encrypt.

---

## 4. SQLite Database Backup & Restoration

### Safe Online Backup (Without Stopping Server)
SQLite WAL mode allows online backups while live predictions are being served:
```bash
# Using the sqlite3 online backup command
sqlite3 /data/tipalgo.db ".backup '/data/tipalgo_backup_$(date +%Y%m%d_%H%M%S).db'"
```

### Database Restore Procedure
In the event of accidental data corruption:
```bash
# 1. Stop service in Railway dashboard
# 2. In terminal, replace database file with backup
cp /data/tipalgo_backup_YYYYMMDD_HHMMSS.db /data/tipalgo.db
# 3. Restart service
```

---

## 5. Health Monitoring & Diagnostics

Query the public health check:
```bash
curl -s https://yourdomain.com/health | jq
```

**Expected Healthy Response:**
```json
{
  "status": "ok",
  "version": "21.0",
  "database": "connected",
  "screener_model": {
    "loaded": true,
    "active_method": "value_screener_ml"
  },
  "environment": "production"
}
```

---

## 6. Rollback Procedure

If a deployed update contains an unexpected regression:
1. In the Railway dashboard, navigate to **Deployments**.
2. Locate the previous successful deployment.
3. Click the three dots `...` -> **Redeploy**.
4. The previous container image will immediately boot and reattach to the `/data` persistent volume.
