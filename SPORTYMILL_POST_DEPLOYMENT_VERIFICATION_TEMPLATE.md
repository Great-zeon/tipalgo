# SPORTYMILL — POST-DEPLOYMENT VERIFICATION TEMPLATE

**Document Reference:** `SPORTYMILL_POST_DEPLOYMENT_VERIFICATION_TEMPLATE.md`  
**Execution Context:** Production Application Smoke Testing (Post-Release)  
**Target URL:** `https://sporty-cn2q.onrender.com`  

---

## 1. POST-DEPLOYMENT SMOKE SUITE OVERVIEW

This template provides the exact operational validation tests to run immediately after deployment of commit `3499770` to production.

```mermaid
flowchart LR
    A[Step 1: Healthcheck Telemetry] --> B[Step 2: Admin Settlement Security]
    B --> C[Step 3: Live Recommendation Ingestion]
    C --> D[Step 4: History & Backtest Separation]
    D --> E[Step 5: OCC Settlement & Financial Audit]
```

---

## 2. VERIFICATION PROCEDURE & RECORDING FORM

### Test 1: Health Telemetry & Dependency Connectivity
```bash
curl -s -i https://sporty-cn2q.onrender.com/health
```

#### Evaluation Checklist:
- [ ] HTTP Status Code is `200 OK`.
- [ ] Response Body includes `{"status": "ok", "database": "connected"}`.
- [ ] Latency is $< 200\text{ ms}$.
- [ ] Response headers include security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`.

---

### Test 2: Administrative Settlement Security & Authorization
```bash
# A. Negative Test: Call without token
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
  https://sporty-cn2q.onrender.com/admin/settle-live-predictions

# Expected Response:
# {"detail": "Invalid or missing admin token"}
# HTTP_STATUS: 401

# B. Positive Test: Call with valid admin token
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
  https://sporty-cn2q.onrender.com/admin/settle-live-predictions \
  -H "X-Admin-Token: $ADMIN_TOKEN"

# Expected Response:
# {"status": "success", "report": {"processed": 0, "settled": 0, ...}}
# HTTP_STATUS: 200
```

#### Evaluation Checklist:
- [ ] Unauthorized request is rejected with HTTP `401 Unauthorized`.
- [ ] Authorized request executes successfully with HTTP `200 OK`.
- [ ] Settlement report returns structured accounting summary.

---

### Test 3: Genuine Live Fixture Integrity & Pre-Kickoff Ingestion
1. Navigate to `https://sporty-cn2q.onrender.com` in a clean browser session.
2. Open the slate of matches scheduled for today before kickoff.
3. Query the production database:
   ```sql
   SELECT 
       id, 
       provider_fixture_id, 
       endpoint, 
       home_team, 
       away_team, 
       match_date, 
       confidence_percent, 
       created_at, 
       source_classification 
   FROM prediction_logs 
   WHERE source_classification = 'live_recorded' 
   ORDER BY id DESC LIMIT 10;
   ```

#### Evaluation Checklist:
- [ ] Pre-kickoff predictions persist with `source_classification = 'live_recorded'`.
- [ ] `created_at` timestamp strictly precedes match kickoff `match_date`.
- [ ] Zero synthetic, demo, or fallback fixtures appear in actionable recommendation cards.
- [ ] Fixture identities match real football fixtures from the configured data provider.

---

### Test 4: Historical Data Isolation & Backtest Separation
Query the prediction history API and verify category isolation:
```bash
curl -s "https://sporty-cn2q.onrender.com/api/prediction-history?page=1&limit=20" | jq .
```

#### Evaluation Checklist:
- [ ] Historical backtests (`provider_verified`, 366 records) remain clearly separated.
- [ ] Retrospective predictions are never falsely labeled as pre-match tips.
- [ ] If no live fixtures have completed yet, the live ledger reports a clear, transparent empty state or pending status without fabricating settlement records.

---

### Test 5: External Financial & Settlement Idempotency Safeguards
1. **Database OCC Protection:** Verify that settlement updates require `outcome = 'pending'`.
2. **Payment Gateway Separation:** Ensure payment webhook events (Paystack/Stripe) are recorded in `bet_slips` or payment transaction ledgers with independent idempotency keys:
   ```sql
   SELECT count(*) FROM bet_slips WHERE status = 'paid';
   ```
3. **No Unverified Settlement:** Confirm no match is settled without verified official scores from the football data provider.
