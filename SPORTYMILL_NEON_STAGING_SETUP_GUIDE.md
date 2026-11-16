# SPORTYMILL — NEON SERVERLESS POSTGRESQL STAGING SETUP GUIDE

**Document Reference:** `SPORTYMILL_NEON_STAGING_SETUP_GUIDE.md`  
**Target Platform:** Neon Serverless PostgreSQL ([neon.tech](https://neon.tech))  
**Purpose:** Long-Term Staging Database Infrastructure (Replacing Ephemeral Render Staging)  

---

## 1. WHY NEON FOR LONG-TERM SPORTYMILL STAGING?

Render's free PostgreSQL tier automatically deletes databases after **30 days** (+14-day grace period) and provides no automated backups. 

**Neon Serverless PostgreSQL** provides an optimal long-term staging environment:
- **Zero Automatic Expiration:** Staging databases remain permanently available without 30-day deletion.
- **Compute Scaling to Zero:** Neon scales compute to zero during idle periods, consuming negligible compute hours.
- **Instant Branching:** Allows creating isolated staging clones of the database schema within milliseconds for testing migrations without risking baseline staging data.
- **Generous Free Allocation:** 1 GB storage, 100 compute-hours/month, and up to 10 project branches.

---

## 2. MANDATORY POSTGRESQL VERSION-PARITY POLICY

> [!IMPORTANT]
> Staging verification is only authoritative when executed against an environment that matches production's SQL dialect, behavioral semantics, and concurrency guarantees. Staging verification on a different major version must **never** be represented as production-version parity.

### Version Parity Requirements:
1. **Render Staging Baseline:** The completed staging verification was executed against **PostgreSQL 18.6** (`PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2) on x86_64-pc-linux-gnu, 64-bit`).
2. **Production Version Confirmation:** Production PostgreSQL version must be independently determined before provisioning long-term staging. **Do not assume production uses PostgreSQL 18 merely because Render staging defaulted to 18.6.**
3. **Parity Preference:** Neon staging must match production's PostgreSQL major version wherever supported.
4. **Mismatch Escalation:** Any unavoidable version difference between production and Neon staging must be recorded with a documented compatibility review before running verification.

### Pre-Provisioning Version Determination Procedure:
Before creating the Neon staging database:
1. **Query Production Version:** During the authorized pre-release inspection, run `SELECT version();` on the production database.
2. **Inspect Existing Staging Version:** Acknowledge that prior staging evidence was collected on PostgreSQL 18.6.
3. **Check Neon Version Availability:** Check which PostgreSQL major versions (e.g. 15, 16, 17) are currently offered by Neon in your target region.
4. **Select Target Version:**
   * If production is PostgreSQL 16: select PostgreSQL 16 on Neon.
   * If production is PostgreSQL 17: select PostgreSQL 17 on Neon.
   * If production is PostgreSQL 18: select PostgreSQL 18 (or latest available version with compatibility sign-off).
5. **Document Invariant:** If Neon does not yet offer the exact production major version, document any minor version delta and review extension/DDL compatibility before running the harness.

---

## 3. STEP-BY-STEP NEON PROVISIONING GUIDE

*(Note: Do NOT provision or modify Neon during this documentation task; follow these steps when staging is formally provisioned).*

### Step 1: Create a Dedicated Staging Project on Neon
1. Log in to your Neon console at [console.neon.tech](https://console.neon.tech).
2. Click **Create Project**.
3. Configure project settings:
   * **Project Name:** `sportymill-staging`
   * **PostgreSQL Version:** Select the major version matching production (determined via Section 2).
   * **Region:** Select the region closest to your Render service (e.g., `AWS US East (N. Virginia)` or `AWS EU (Frankfurt)`).
   * **Compute Size:** `0.25 CU` (Default starter compute).
4. Click **Create Project**.

### Step 2: Configure Staging Database & Role
1. In the Neon Project Dashboard, navigate to **Databases**:
   * Click **New Database**.
   * Database Name: `tipalgo_staging`
2. Navigate to **Roles**:
   * Click **New Role**.
   * Username: `sporty_staging_user`
   * Generate a secure password.

### Step 3: Obtain Staging Connection String
1. On the project overview page, select:
   * Database: `tipalgo_staging`
   * Role: `sporty_staging_user`
2. Copy the **Pooled** or **Direct** connection string:
   * Format: `postgresql://sporty_staging_user:PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/tipalgo_staging?sslmode=require`
3. **Security Invariant:** Never commit this string to Git or reuse production credentials.

---

## 4. EXECUTING POSTGRESQL VERIFICATION AGAINST NEON

Once provisioned, execute the automated 14-step staging verification harness against Neon:

```bash
# 1. Ensure you are on the integration branch
cd /Users/macos/Downloads/tipalgo
git checkout integration-controlled

# 2. Set the Neon Connection String in your terminal
export DATABASE_URL="postgresql://sporty_staging_user:YOUR_NEON_PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/tipalgo_staging?sslmode=require"

# 3. Run the PostgreSQL Staging Verification Harness
python scripts/verify_postgres_staging.py
```

### Expected Output on Neon:
The harness will automatically detect **Scenario A** (Clean Staging DB), initialize `PREDICTION_LOGS_PROD_DDL`, apply Migration 002, run the 11-market test, the 10-worker duplicate suppression test, the 5-connection OCC settlement test, exact PK cleanup, and confirm zero leakage.

---

## 5. NEON DATABASE BRANCHING WORKFLOW (BEST PRACTICES)

Neon enables instant database branching for preview deployments and integration testing:

```mermaid
flowchart TD
    A[Neon Root Branch: staging-main] --> B[Create Branch: feat-settlement-test]
    B --> C[Execute Destructive / Concurrency Tests on Child Branch]
    C --> D[Delete Child Branch: Zero Impact on staging-main]
```

### Creating an Isolated Test Branch via Neon CLI:
```bash
# Install Neon CLI if desired
npm install -g neonctl
neonctl auth

# Create an ephemeral test branch in 2 seconds
neonctl branches create --name test-settlement-run
```

---

## 6. OPERATIONAL GOVERNANCE & BOUNDARIES

1. **Strict Credential Separation:** Production credentials must NEVER be entered into Neon, and Neon staging credentials must NEVER be placed in production environment variables.
2. **No Production Data Ingestion:** Production customer records or live payment data must never be dumped into staging without formal sanitization and explicit written approval.
3. **Staging is NOT a Backup:** The staging database is an isolated testing ground, not a backup repository for production data. Production backups must remain managed under the production cluster's automated snapshot retention policy.
