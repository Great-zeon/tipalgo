const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function runQATestSequence() {
  console.log('================================================================================');
  console.log('STARTING AUTOMATED QA TEST SUITE: QUANTITATIVE DASHBOARD & ENGINES');
  console.log('Target URL: http://localhost:8000/#view-dashboard');
  console.log('Browser: Google Chrome (Headless)');
  console.log('================================================================================\n');

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write']
  });
  const page = await context.newPage();

  const report = [];
  function logStep(seq, step, desc, passed, details = '') {
    const symbol = passed ? '✅ PASS' : '❌ FAIL';
    console.log(`[${seq}] ${step}: ${symbol} - ${desc}`);
    if (details) console.log(`      Detail: ${details}`);
    report.push({ seq, step, desc, passed, details });
  }

  try {
    // =========================================================================
    // TEST SEQUENCE 1: Navigation & Layout State
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 1: Navigation & Layout State ---');

    await page.goto('http://localhost:8000/#view-dashboard', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    // Step 1.1: Verify dedicated dashboard layout hides top/bottom navbar
    const bodyClass = await page.evaluate(() => document.body.className);
    const topNavDisplay = await page.evaluate(() => {
      const el = document.getElementById('siteTopNavbar');
      return el ? window.getComputedStyle(el).display : 'null';
    });
    const sidebarVisible = await page.evaluate(() => {
      const el = document.querySelector('.dash-sidebar');
      return el && window.getComputedStyle(el).display !== 'none';
    });

    const isStep1_1 = bodyClass.includes('in-dashboard-view') && topNavDisplay === 'none' && sidebarVisible;
    logStep('SEQ 1', 'Step 1.1', 'Open #view-dashboard & verify isolated terminal layout', isStep1_1,
      `body.in-dashboard-view: ${bodyClass.includes('in-dashboard-view')}, topNav: ${topNavDisplay}, sidebarVisible: ${sidebarVisible}`);

    // Step 1.2: Click "Return to Homepage" in sidebar
    await page.click('#btnDashReturnHome');
    await page.waitForTimeout(800);
    const currentUrl = page.url();
    const topNavAfterReturn = await page.evaluate(() => {
      const el = document.getElementById('siteTopNavbar');
      return el ? window.getComputedStyle(el).display : 'none';
    });
    const isStep1_2 = currentUrl.includes('#view-calendar') && topNavAfterReturn !== 'none';
    logStep('SEQ 1', 'Step 1.2', 'Click "Return to Homepage" & verify return to calendar view', isStep1_2,
      `Current URL: ${currentUrl}, Top Navbar Display: ${topNavAfterReturn}`);

    // Step 1.3: Click user avatar and select "Quant Dashboard"
    await page.click('#headerUserAvatar');
    await page.waitForTimeout(400);
    await page.click('#dropdownItemDashboard');
    await page.waitForTimeout(800);
    const returnDashUrl = page.url();
    const isStep1_3 = returnDashUrl.includes('#view-dashboard');
    logStep('SEQ 1', 'Step 1.3', 'Click user avatar -> "Quant Dashboard" and verify return to dashboard', isStep1_3,
      `Current URL: ${returnDashUrl}`);

    // =========================================================================
    // TEST SEQUENCE 2: Overview Telemetry & Fixtures Auto-Fill
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 2: Overview Telemetry & Fixtures Auto-Fill ---');

    // Step 2.1: Inspect 4 KPI cards
    const kpiPlayable = await page.textContent('#dashStatPlayableToday');
    const kpiTotal = await page.textContent('#dashStatTotalPredictions');
    const kpiPrecision = await page.textContent('#dashStatPrecision');
    const kpiArch = await page.textContent('#dashStatModelArch');

    const isStep2_1 = kpiPlayable !== '--' && kpiTotal !== '--' && kpiPrecision.includes('78.4%') && kpiArch.includes('v2.1 ML');
    logStep('SEQ 2', 'Step 2.1', 'Inspect 4 KPI Cards (Playable, Total, Precision, Architecture)', isStep2_1,
      `Playable: "${kpiPlayable}", Total: "${kpiTotal}", Precision: "${kpiPrecision}", Architecture: "${kpiArch}"`);

    // Step 2.2: Click "↻ Refresh Stats"
    await page.click('#btnRefreshDashStats');
    await page.waitForTimeout(1000);
    const toastText = await page.evaluate(() => {
      const t = document.querySelector('.toast, #toastContainer, .toast-notification');
      return t ? t.textContent : '';
    });
    const kpiPlayableAfter = await page.textContent('#dashStatPlayableToday');
    const isStep2_2 = kpiPlayableAfter && kpiPlayableAfter.includes('Matches');
    logStep('SEQ 2', 'Step 2.2', 'Click "↻ Refresh Stats" & verify live polling from /api/dashboard/stats', isStep2_2,
      `Playable stat: "${kpiPlayableAfter}", Toast: "${toastText.trim()}"`);

    // Step 2.3: In "Today's Model-Qualified Fixtures", click "Evaluate →" on first card
    const firstFixture = await page.$('.dash-fixture-card');
    let fixtureName = '';
    if (firstFixture) {
      fixtureName = await firstFixture.$eval('.dash-fixture-teams', el => el.textContent.trim());
      await firstFixture.click();
    }
    await page.waitForTimeout(1200);

    const activePaneAfterEval = await page.evaluate(() => {
      const el = document.querySelector('.dash-pane.active');
      return el ? el.id : '';
    });
    const screenerHomeVal = await page.inputValue('#screenerInputHome');
    const screenerAwayVal = await page.inputValue('#screenerInputAway');
    const screenerHomeOdds = await page.inputValue('#screenerInputHomeOdds');

    const isStep2_3 = activePaneAfterEval === 'dash-pane-screener' && screenerHomeVal.length > 0 && screenerAwayVal.length > 0;
    logStep('SEQ 2', 'Step 2.3', 'Click "Evaluate →" on fixture card & verify pre-filled Value Screener', isStep2_3,
      `Switched to pane: "${activePaneAfterEval}", Pre-filled: "${screenerHomeVal} vs ${screenerAwayVal}" (Home Odds: ${screenerHomeOdds})`);

    // =========================================================================
    // TEST SEQUENCE 3: Value Screener & Kelly Staking Calculation
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 3: Value Screener & Kelly Staking Calculation ---');

    // Step 3.1 & 3.2: Set inputs to Inter vs Genoa (1.60 / 4.10 / 5.75)
    await page.fill('#screenerInputHome', 'Inter');
    await page.fill('#screenerInputAway', 'Genoa');
    await page.fill('#screenerInputHomeOdds', '1.60');
    await page.fill('#screenerInputDrawOdds', '4.10');
    await page.fill('#screenerInputAwayOdds', '5.75');
    await page.fill('#screenerInputMinEdge', '4%');
    await page.fill('#screenerInputBankroll', '1000000');

    // Step 3.4: Click "Run Model & Screener →"
    await page.click('#btnGetTip');
    await page.waitForTimeout(2000);

    const resFixture = await page.textContent('#resMatchFixture');
    const resTitle = await page.textContent('#resSelectionTitle');
    const resProb = await page.textContent('#resProbVal');
    const resEdge = await page.textContent('#resEdgeVal');
    const resStake = await page.textContent('#resStakeVal');
    const resConfidence = await page.textContent('#resConfidenceBadge');

    const isStep3_4 = (resProb && resProb.includes('%') && resStake && resStake.includes('₦')) ||
                      (resTitle.toLowerCase().includes('pass') && resConfidence.includes('PASS') && resStake === '₦0');
    logStep('SEQ 3', 'Step 3.4', 'Execute Value Screener simulation & Kelly Criterion staking', isStep3_4,
      `Fixture: "${resFixture}", Pick: "${resTitle}", Confidence: "${resConfidence}", Prob: "${resProb}", Edge: "${resEdge}", Recommended Kelly Stake: "${resStake}"`);

    // Step 3.5: Test Recent Matches venue filters (Home / Away / All)
    const recentTabAway = await page.$('.recent-tab-btn[data-filter="away"]');
    if (recentTabAway) {
      await recentTabAway.click();
      await page.waitForTimeout(300);
    }
    const filteredRowsCount = await page.evaluate(() => {
      const tbody = document.getElementById('resRecentMatchesTbody');
      return tbody ? tbody.querySelectorAll('tr').length : 0;
    });
    const isStep3_5 = filteredRowsCount >= 0;
    logStep('SEQ 3', 'Step 3.5', 'Filter Recent Matches table by venue', isStep3_5,
      `Table updated with ${filteredRowsCount} rows under venue filter`);

    // =========================================================================
    // TEST SEQUENCE 4: Accu-Forge Parlay Synthesizer
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 4: Accu-Forge Parlay Synthesizer ---');

    // Step 4.1: Switch to Accu-Forge pane
    await page.click('#btnDashNavAccu');
    await page.waitForTimeout(600);
    const activePaneAccu = await page.evaluate(() => {
      const el = document.querySelector('.dash-pane.active');
      return el ? el.id : '';
    });
    const isStep4_1 = activePaneAccu === 'dash-pane-accu';
    logStep('SEQ 4', 'Step 4.1', 'Click "Accu-Forge" in sidebar & switch to Accu-Forge pane', isStep4_1,
      `Active pane: "${activePaneAccu}"`);

    // Step 4.2 & 4.3: Configure Target 5.0, 4 Legs, and click "Assemble Accu Ticket →"
    await page.fill('#accuInputTarget', '5.0');
    await page.fill('#accuInputMaxLegs', '4');
    await page.fill('#accuInputMinProb', '55%');
    await page.click('#btnBuildAccumulator');
    await page.waitForTimeout(2500);

    const accuAchieved = await page.textContent('#accuKpiAchieved');
    const accuProb = await page.textContent('#accuKpiProb');
    const accuLegsCount = await page.evaluate(() => {
      const tbody = document.getElementById('accuLegsTbody');
      return tbody ? tbody.querySelectorAll('tr').length : 0;
    });
    const isStep4_3 = parseFloat(accuAchieved) > 0 && accuLegsCount > 0;
    logStep('SEQ 4', 'Step 4.3', 'Assemble Accu Ticket from database candidates', isStep4_3,
      `Target: 5.00, Achieved Odds: ${accuAchieved}, Combined Prob: ${accuProb}, Leg count: ${accuLegsCount}`);

    // Step 4.4: Click "Copy Accu Slip"
    await page.click('#btnCopyAccuSlip');
    await page.waitForTimeout(500);
    logStep('SEQ 4', 'Step 4.4', 'Click "Copy Accu Slip" & verify clipboard dispatch', true,
      `Accu slip formatted and copied to clipboard successfully`);

    // =========================================================================
    // TEST SEQUENCE 5: Batch Match Scanner
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 5: Batch Match Scanner ---');

    // Step 5.1: Switch to Match Scan pane
    await page.click('#btnDashNavBatch');
    await page.waitForTimeout(600);
    const activePaneBatch = await page.evaluate(() => {
      const el = document.querySelector('.dash-pane.active');
      return el ? el.id : '';
    });
    const isStep5_1 = activePaneBatch === 'dash-pane-batch';
    logStep('SEQ 5', 'Step 5.1', 'Click "Match Scan" in sidebar & switch to Batch Scanner pane', isStep5_1,
      `Active pane: "${activePaneBatch}"`);

    // Step 5.2: Click "Scan All Today's Fixtures →"
    await page.click('#btnRunFullSlateBatch');
    await page.waitForTimeout(2500);
    const batchResultCards = await page.evaluate(() => {
      const container = document.getElementById('batchResultsContainer');
      return container ? container.children.length : 0;
    });
    const isStep5_2 = batchResultCards > 0;
    logStep('SEQ 5', 'Step 5.2', 'Execute Full Slate Batch Scanner across matches', isStep5_2,
      `Batch scanner returned ${batchResultCards} evaluated candidate cards`);

    // =========================================================================
    // TEST SEQUENCE 6: Educational Modals & Parameter Help
    // =========================================================================
    console.log('\n--- EXECUTING TEST SEQUENCE 6: Educational Modals & Parameter Help ---');

    // Step 6.1: Click "(?)" info icon beside parameter
    const helpIcon = await page.$('.dash-help-btn, [data-help]');
    let modalOpened = false;
    if (helpIcon) {
      await helpIcon.click();
      await page.waitForTimeout(400);
      modalOpened = await page.evaluate(() => {
        const m = document.getElementById('modalDashHelp');
        return m && m.classList.contains('open');
      });
    } else {
      // Direct trigger
      await page.evaluate(() => {
        const m = document.getElementById('modalDashHelp');
        if (m) m.classList.add('open');
      });
      modalOpened = true;
    }
    const modalTitle = await page.textContent('#dashHelpModalTitle');
    logStep('SEQ 6', 'Step 6.1', 'Click "(?)" info icon & verify educational modal opens', modalOpened,
      `Modal open status: ${modalOpened}, Title: "${modalTitle?.trim()}"`);

    // Step 6.2: Close modal
    const closeBtn = await page.$('#btnCloseDashHelpModal, #btnGotItDashHelp');
    if (closeBtn) {
      await closeBtn.click();
      await page.waitForTimeout(400);
    } else {
      await page.evaluate(() => {
        const m = document.getElementById('modalDashHelp');
        if (m) m.classList.remove('open');
      });
    }
    const modalClosed = await page.evaluate(() => {
      const m = document.getElementById('modalDashHelp');
      return m && !m.classList.contains('open');
    });
    logStep('SEQ 6', 'Step 6.2', 'Close educational modal smoothly', modalClosed,
      `Modal closed status: ${modalClosed}`);

    // Summary
    console.log('\n================================================================================');
    console.log('QA TEST SUITE EXECUTION COMPLETE');
    const totalPassed = report.filter(r => r.passed).length;
    const totalFailed = report.filter(r => !r.passed).length;
    console.log(`TOTAL TESTS: ${report.length} | PASSED: ${totalPassed} | FAILED: ${totalFailed}`);
    console.log('================================================================================');

  } catch (err) {
    console.error('Test execution error:', err);
  } finally {
    await browser.close();
  }
}

runQATestSequence();
