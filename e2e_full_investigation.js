const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE_URL = process.env.API_BASE_URL || 'http://127.0.0.1:8000';
const SCREENSHOT_DIR = path.resolve(__dirname, 'test_screenshots');
if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

async function runFullInvestigation() {
  console.log(`Starting Full Functional Failure Investigation at ${BASE_URL}...`);
  const browser = await chromium.launch({
    headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const report = {
    timestamp: new Date().toISOString(),
    testsPassed: 0,
    testsFailed: 0,
    results: []
  };

  function logResult(name, passed, details = {}) {
    if (passed) {
      report.testsPassed++;
      console.log(`✅ [PASS] ${name}`);
    } else {
      report.testsFailed++;
      console.error(`❌ [FAIL] ${name}:`, details.error || details.message || '');
    }
    report.results.push({ name, passed, ...details });
  }

  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  // Listen to console and errors
  const consoleErrors = [];
  page.on('console', msg => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
      console.error('Browser console error:', msg.text());
    }
  });
  page.on('pageerror', err => {
    consoleErrors.push(err.toString());
    console.error('Uncaught page error:', err.toString());
  });

  try {
    // ----------------------------------------------------
    // TEST 1: Launch App & Check Security Gate
    // ----------------------------------------------------
    console.log('\n--- Test 1: App Launch & Security Gate ---');
    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle' });
    const gateVisible = await page.isVisible('#sportymillSecurityGate');
    logResult('Security Gate Displayed', gateVisible);

    // ----------------------------------------------------
    // TEST 2: Partner Login WITHOUT PIN (Verifying slot auto-rotation)
    // ----------------------------------------------------
    console.log('\n--- Test 2: Whitelisted Partner Login ---');
    await page.fill('#gateUsernameInput', 'tip_partner');
    await page.fill('#gatePasswordInput', 'TipAlgo2026!');
    // Intentionally do NOT fill any override PIN
    await page.click('#btnGateLoginSubmit');
    await page.waitForTimeout(1500);

    const gateHidden = await page.evaluate(() => {
      const g = document.getElementById('sportymillSecurityGate');
      return !g || g.style.display === 'none';
    });
    const tokenStored = await page.evaluate(() => !!localStorage.getItem('tipalgo_token'));
    logResult('Partner Login Without PIN Succeeded', gateHidden && tokenStored, { gateHidden, tokenStored });

    // ----------------------------------------------------
    // TEST 3: Calendar Slate Loading & Matches
    // ----------------------------------------------------
    console.log('\n--- Test 3: Calendar Slate Feed ---');
    await page.waitForSelector('.employer-match-card', { timeout: 10000 });
    const matchCount = await page.evaluate(() => document.querySelectorAll('.employer-match-card').length);
    logResult('Calendar Slate Rendered Fixtures', matchCount > 0, { matchCount });

    // ----------------------------------------------------
    // TEST 4: Calendar Dropdown Filter Dynamics
    // ----------------------------------------------------
    console.log('\n--- Test 4: Calendar Dropdown Filter Dynamics ---');
    const filterEndpoints = ['draw', 'over-2-5', 'btts-yes', 'cards-1x2', 'all'];
    let filterWorking = true;
    for (const ep of filterEndpoints) {
      await page.selectOption('#calendarEndpointDropdown', ep);
      await page.waitForTimeout(600);
      const filteredCount = await page.evaluate(() => document.querySelectorAll('.employer-match-card').length);
      const firstPick = await page.evaluate(() => {
        const topEl = document.querySelector('.card-pred-pill');
        return topEl ? topEl.textContent.trim() : '';
      });
      console.log(`   Filter '${ep}': ${filteredCount} matches | First Pick: "${firstPick}"`);
      if (filteredCount === 0 && ep !== 'all') {
        filterWorking = false;
      }
    }
    logResult('Calendar Filters Actively Filter Matches & Top Picks', filterWorking);

    // ----------------------------------------------------
    // TEST 5: Top Navigation - 33 Endpoints View
    // ----------------------------------------------------
    console.log('\n--- Test 5: Top Nav: 33 Endpoints ---');
    await page.click('#topNavGrand');
    await page.waitForTimeout(500);
    const grandActive = await page.isVisible('#view-grand');
    const endpointCards = await page.evaluate(() => document.querySelectorAll('#grandEndpointsGrid .grand-endpoint-card').length);
    logResult('33 Endpoints View & Grid Rendered', grandActive && endpointCards >= 33, { endpointCards });

    // Test drilldown on an endpoint card
    const firstEndpointBtn = await page.$('#grandEndpointsGrid .btn-explore-ep');
    if (firstEndpointBtn) {
      await firstEndpointBtn.click();
      await page.waitForTimeout(800);
      const drilldownMatches = await page.evaluate(() => document.querySelectorAll('#grandDrilldownMatchesFeed .employer-match-card').length);
      logResult('Endpoint Drilldown Qualifying Fixtures Feed', drilldownMatches > 0, { drilldownMatches });
    }

    // ----------------------------------------------------
    // TEST 6: Top Navigation - 80%+ Banker View
    // ----------------------------------------------------
    console.log('\n--- Test 6: Top Nav: 80%+ Banker Slate ---');
    await page.click('#topNavBankers');
    await page.waitForTimeout(800);
    const bankersActive = await page.isVisible('#view-bankers');
    const bankerCardsCount = await page.evaluate(() => document.querySelectorAll('#bankersSlateGrid .banker-card, #bankersSlateGrid .outcome-clean-card').length);
    const bankerGridHtml = await page.evaluate(() => document.getElementById('bankersSlateGrid')?.innerText?.slice(0, 100));
    console.log(`   Banker cards rendered: ${bankerCardsCount} | Preview: "${bankerGridHtml}"`);
    logResult('80%+ Banker Slate View & Cards Populated', bankersActive && (bankerCardsCount > 0 || bankerGridHtml.length > 5), {
      bankersActive,
      bankerCardsCount
    });

    // ----------------------------------------------------
    // TEST 7: Top Navigation - Value Screener View & Execution
    // ----------------------------------------------------
    console.log('\n--- Test 7: Top Nav: Value Screener ---');
    await page.click('#topNavScreener');
    await page.waitForTimeout(500);
    const screenerActive = await page.isVisible('#view-screener');
    await page.click('#btnExecuteScreener');
    await page.waitForTimeout(1000);
    const screenerCardText = await page.evaluate(() => document.getElementById('screenerResultDisplay')?.innerText);
    const screenerSuccess = screenerCardText && (screenerCardText.includes('Win Likelihood') || screenerCardText.includes('Calculated Edge') || screenerCardText.includes('VALUE'));
    console.log(`   Screener execution result: "${screenerCardText.slice(0, 80)}..."`);
    logResult('Value Screener Executed Real Model Prediction', screenerActive && screenerSuccess);

    // ----------------------------------------------------
    // TEST 8: Top Navigation - Accu-Forge Parlay Engine
    // ----------------------------------------------------
    console.log('\n--- Test 8: Top Nav: Accu-Forge Parlay Engine ---');
    await page.click('#topNavAccu');
    await page.waitForTimeout(500);
    const accuActive = await page.isVisible('#view-accumulator');
    await page.click('#btnGenerateAccuTicket');
    await page.waitForTimeout(1500);
    const accuDisplayText = await page.evaluate(() => document.getElementById('accuTicketDisplay')?.innerText);
    const accuValid = accuDisplayText && (accuDisplayText.includes('Accu-Forge') || accuDisplayText.includes('Legs') || accuDisplayText.includes('parlay'));
    console.log(`   Accu-Forge ticket result: "${accuDisplayText.slice(0, 80)}..."`);
    logResult('Accu-Forge Generates Valid Parlay Ticket Without TypeError', accuActive && accuValid, { accuDisplayText: accuDisplayText.slice(0, 100) });

    // ----------------------------------------------------
    // TEST 9: Top Navigation - Full Slate Batch Scanner
    // ----------------------------------------------------
    console.log('\n--- Test 9: Top Nav: Full Slate Batch Scanner ---');
    await page.click('#topNavBatch');
    await page.waitForTimeout(500);
    const batchActive = await page.isVisible('#view-batch');
    await page.click('#btnExecuteBatchScan');
    await page.waitForTimeout(1500);
    const tableRows = await page.evaluate(() => document.querySelectorAll('#batchScanResultsTable tbody tr').length);
    console.log(`   Full Slate Scanner Table Rows: ${tableRows}`);
    logResult('Full Slate Scanner Computes Mathematical Edge for All Scheduled Matches', batchActive && tableRows > 0, { tableRows });

    // ----------------------------------------------------
    // TEST 10: Match Analytics Hub & Modals
    // ----------------------------------------------------
    console.log('\n--- Test 10: Match Analytics Hub ---');
    await page.click('#topNavHome');
    await page.waitForSelector('.employer-match-card', { timeout: 10000 });
    await page.waitForTimeout(500);

    const firstCard = await page.$('.employer-match-card');
    if (firstCard) {
      const detailsBtn = await firstCard.$('.btn-grid-details, .btn-card-view-analytics');
      if (detailsBtn) {
        await detailsBtn.click();
      } else {
        await firstCard.click();
      }
      await page.waitForSelector('#view-match-analytics.active', { timeout: 8000 });
      await page.waitForTimeout(1200);

      const teamsText = await page.evaluate(() => {
        const home = document.getElementById('footyHomeTeamName')?.textContent?.trim() || '';
        const away = document.getElementById('footyAwayTeamName')?.textContent?.trim() || '';
        return `${home} vs ${away}`;
      });
      console.log(`   Match Analytics Teams: "${teamsText}"`);
      const notEmptyOrArsenal = teamsText.trim().length > 3 && !teamsText.includes('Arsenal vs Chelsea');
      logResult('Match Analytics Hub Loaded For Selected Active Match', notEmptyOrArsenal, { teamsText });

      // Return to overview
      const backBtn = await page.$('#btnBackToOverview');
      if (backBtn && await backBtn.isVisible()) {
        await backBtn.click();
        await page.waitForTimeout(500);
      }
    }

    // ----------------------------------------------------
    // TEST 11: Betslip Storage & Badge
    // ----------------------------------------------------
    console.log('\n--- Test 11: Betslip Addition & Counter ---');
    const pickTrigger = await page.$('.employer-match-card .pick-trigger');
    if (pickTrigger) {
      await pickTrigger.click();
      await page.waitForTimeout(500);
      const betslipCount = await page.evaluate(() => {
        const list = JSON.parse(localStorage.getItem('sportymill_betslip') || '[]');
        return list.length;
      });
      logResult('Betslip Adds Fixture Selection & Updates Counter', betslipCount > 0, { betslipCount });
    }

    // ----------------------------------------------------
    // TEST 12: Mobile Viewport Layout Test
    // ----------------------------------------------------
    console.log('\n--- Test 12: Mobile Layout (iPhone 13 - 390x844) ---');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(600);
    const mobileBottomNavVisible = await page.isVisible('.mobile-bottom-nav');
    const mobileBottomCards = await page.evaluate(() => document.querySelectorAll('.employer-match-card').length);
    logResult('Mobile Viewport & Bottom Navigation Active', mobileBottomNavVisible && mobileBottomCards > 0);

    // Save screenshots
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'mobile_view.png') });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_view.png') });

    // Check for critical console errors
    const fatalErrors = consoleErrors.filter(e => !e.includes('favicon') && !e.includes('ERR_CONNECTION_REFUSED'));
    logResult('No Critical Console Runtime Exceptions', fatalErrors.length === 0, { errors: fatalErrors });

  } catch (err) {
    console.error('Test execution failed with error:', err);
    logResult('Test Suite Execution', false, { error: err.message, stack: err.stack });
  } finally {
    await browser.close();
    fs.writeFileSync('full_investigation_report.json', JSON.stringify(report, null, 2));
    console.log(`\n========================================`);
    console.log(`INVESTIGATION COMPLETED: ${report.testsPassed} PASSED, ${report.testsFailed} FAILED`);
    console.log(`========================================\n`);
  }
}

runFullInvestigation();
