#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');

async function main() {
  const [url, playwrightModule, browserPath, outputDir] = process.argv.slice(2);
  assert(url && playwrightModule && browserPath && outputDir,
    'Usage: node evolution-ui.cjs URL PLAYWRIGHT_MODULE BROWSER_PATH OUTPUT_DIR');
  const { chromium } = require(playwrightModule);
  await fs.mkdir(outputDir, { recursive: true });
  const result = { url, checkedAt: new Date().toISOString(), checks: [], screenshots: [], pageErrors: [] };
  const browser = await chromium.launch({ executablePath: browserPath, headless: true });
  let fixtureServer;
  const check = (name, details) => result.checks.push({ name, passed: true, ...details });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    page.on('pageerror', e => result.pageErrors.push(e.message));
    const api = await page.request.get(new URL('/api/state', url).href);
    assert(api.ok(), 'Real API state responds successfully');
    const state = await api.json();
    assert(state.tasks.length, 'Real pilot must contain tasks');
    await page.goto(url);
    await page.locator('#content').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.brand-name').textContent(), '东合项目进化史');
    assert.equal(await page.locator('#project-root').textContent(), state.projectRoot);
    assert.equal(await page.locator('.task').count(), state.tasks.length);
    for (const task of state.tasks) {
      const card = page.locator('.task').filter({ has: page.locator('.task-id', { hasText: task.id }) });
      assert.equal(await card.locator('h3').textContent(), task.title);
      assert((await card.locator('.task-status').textContent()).includes(task.status === 'active' ? '进行中' : '已完成'));
      for (const criterion of task.criteria) assert((await card.textContent()).includes(criterion.label));
    }
    assert.equal(Number(await page.locator('#completed-count').textContent()),
      state.tasks.filter(t => t.status === 'completed' && t.verification === 'passed').length);
    check('real-api-task-and-project-visible', { tasks: state.tasks.map(t => ({ id: t.id, status: t.status, verification: t.verification })) });
    const desktop = path.resolve(outputDir, 'evolution-desktop.png');
    await page.screenshot({ path: desktop, fullPage: true }); result.screenshots.push(desktop);

    const receipt = page.getByRole('button', { name: /查看回执|复用回执/ }).first();
    await receipt.click();
    await page.locator('.receipt-result').waitFor();
    assert((await page.locator('.receipt-result').textContent()).includes('退出码'));
    assert.equal(await page.locator('#source-content').textContent(), '');
    await page.getByRole('button', { name: '查看原始回执 ↗', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('source-content').textContent.includes('exitCode'));
    const receiptData = JSON.parse(await page.locator('#source-content').textContent());
    assert.equal(typeof receiptData.exitCode, 'number');
    await page.getByRole('button', { name: '关闭原始记录' }).click();
    assert(await receipt.evaluate(el => el === document.activeElement), 'Original receipt trigger regains focus after raw view');
    check('real-receipt-summary-first-raw-available-and-focus-restored', { exitCode: receiptData.exitCode });

    let historicalFailures = 0;
    for (const task of state.tasks) {
      const card = page.locator('.task').filter({ has: page.locator('.task-id', { hasText: task.id }) });
      for (const [index, event] of (task.events || []).entries()) {
        if (event.kind !== 'failed' || !event.receiptPath) continue;
        historicalFailures++;
        const expected = await (await page.request.get(new URL('/api/receipt?path=' + encodeURIComponent(event.receiptPath), url).href)).json();
        assert.equal(expected.integrity, 'valid');
        assert.notEqual(expected.summary.exitCode, 0);
        const trigger = card.getByRole('button', { name: '查看历史回执 ' + (index + 1) + ' ↗', exact: true });
        await trigger.click();
        await page.locator('.receipt-result.failed').waitFor();
        assert((await page.locator('.receipt-result').textContent()).includes(String(expected.summary.exitCode)));
        for (const name of ['stdout', 'stderr']) {
          assert.equal(await page.locator('.receipt-output').filter({ has: page.getByRole('heading', { name: new RegExp('^' + name) }) }).locator('pre').textContent(), expected.output[name].preview || '（无输出）');
          const original = await (await page.request.get(new URL('/api/source?path=' + encodeURIComponent(expected.output[name].path), url).href)).json();
          assert(original.content.startsWith(expected.output[name].preview), 'Historical preview matches original ' + name);
        }
        if (task.status === 'completed') assert((await card.locator('.task-status').textContent()).includes('已完成'));
        if (historicalFailures === 1) { const proof = path.resolve(outputDir, 'evolution-historical-failure.png'); await page.screenshot({ path: proof, fullPage: true }); result.screenshots.push(proof); }
        await page.keyboard.press('Escape');
        assert(await trigger.evaluate(el => el === document.activeElement));
      }
    }
    assert(historicalFailures >= 3, 'Forms pilot preserves all three historical failures');
    check('real-historical-failures-open-original-output-with-current-status-preserved', { historicalFailures });

    const taskSource = state.tasks[0].sourcePath;
    await page.getByRole('button', { name: '任务原文 ↗' }).first().click();
    await page.waitForFunction(() => document.getElementById('source-content').textContent.length > 0);
    assert.equal(await page.locator('#source-path').textContent(), taskSource);
    const expectedTask = await (await page.request.get(new URL('/api/source?path=' + encodeURIComponent(taskSource), url).href)).json();
    assert.equal(await page.locator('#source-content').textContent(), expectedTask.content);
    await page.keyboard.press('Escape');
    check('real-task-source-matches-api-and-escape-closes');

    assert(state.handoffPath, 'Pilot provides a handoff');
    await page.getByRole('button', { name: /当前交接/ }).click();
    await page.waitForFunction(() => document.getElementById('source-content').textContent.length > 0);
    const expectedHandoff = await (await page.request.get(new URL('/api/source?path=' + encodeURIComponent(state.handoffPath), url).href)).json();
    assert.equal(await page.locator('#source-content').textContent(), expectedHandoff.content);
    await page.keyboard.press('Escape');
    check('real-handoff-source-matches-api');

    await page.setViewportSize({ width: 390, height: 844 });
    const widths = await page.evaluate(() => ({ viewport: innerWidth, body: document.body.scrollWidth, document: document.documentElement.scrollWidth }));
    assert(widths.body <= widths.viewport && widths.document <= widths.viewport, JSON.stringify(widths));
    await receipt.click();
    await page.locator('.receipt-result').waitFor();
    assert(await page.locator('#source-dialog').evaluate(el => el.getBoundingClientRect().right <= innerWidth));
    await page.keyboard.press('Escape');
    const mobile = path.resolve(outputDir, 'evolution-mobile.png');
    await page.screenshot({ path: mobile, fullPage: true }); result.screenshots.push(mobile);
    check('mobile-390px-no-horizontal-overflow-and-source-drawer', widths);

    // A separate ephemeral service exercises counterexamples without modifying pilot data.
    const html = await fs.readFile(path.resolve(__dirname, '../../assets/evolution.html'));
    let fixtureState = { projectRoot: '/fixture/empty-project', generatedAt: '2026-09-05T00:00:00Z', decision: { action: 'stop', reason: 'Fixture: no authorized task' }, tasks: [], handoffPath: null, logPaths: [] };
    let failState = false;
    const unsafe = '<img src=x onerror=alert(1)>' + 'x'.repeat(10000);
    let fixtureReceipt = { integrity: 'valid', summary: { taskId: 'FIX0', criterionId: 'C1', exitCode: 0, argv: ['echo', unsafe], inputCounts: { before: 2, after: 2 }, inputChanged: false }, output: { stdout: { path: 'stdout.txt', preview: unsafe, truncated: true, bytes: 100000 }, stderr: { path: 'stderr.txt', preview: '', truncated: false, bytes: 0 } }, rawPath: 'receipt.json' };

    fixtureServer = http.createServer((req, res) => {
      if (req.url === '/api/state') { res.writeHead(failState ? 503 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(fixtureState)); }
      else if (req.url.startsWith('/api/receipt?')) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(fixtureReceipt)); }
      else if (req.url.startsWith('/api/source?')) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ content: unsafe.repeat(5) })); }
      else { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); }
    });
    await new Promise(resolve => fixtureServer.listen(0, '127.0.0.1', resolve));
    await page.goto('http://127.0.0.1:' + fixtureServer.address().port);
    await page.locator('.empty').waitFor();
    assert((await page.locator('.empty').textContent()).includes('当前已停止'));
    assert.equal(await page.locator('#completed-count').textContent(), '0');
    check('isolated-fixture-empty-is-stopped-not-complete');
    fixtureState = { ...fixtureState, decision: { action: 'recover', reason: 'Fixture: evidence needs repair' }, tasks: ['pending', 'stale', 'invalid'].map((verification, i) => ({ id: 'FIX' + i, title: i === 0 ? '<img src=x onerror=alert(1)>' : verification + ' fixture', status: 'completed', verification, issues: ['Fixture blocker'], criteria: [{ id: 'C1', label: 'Fixture criterion', state: i === 0 ? 'failed' : verification, receiptPath: 'receipt.json' }], events: [{ at: '2026-09-05T00:00:00Z', label: 'Fixture failure preserved', kind: 'failed' }] })) };
    await page.getByRole('button', { name: '↻ 刷新事实' }).click();
    await page.locator('.task').first().waitFor();
    assert.equal(await page.locator('#completed-count').textContent(), '0');
    for (const text of ['待验证', '证据已陈旧', '证据无效', '验证失败']) assert((await page.locator('#journey').textContent()).includes(text));
    assert.equal(await page.locator('.task img').count(), 0);
    assert((await page.locator('.task h3').first().textContent()).includes('<img'));
    check('isolated-fixture-failed-stale-invalid-never-count-as-passed-and-safe-text');
    const fixtureTrigger = page.getByRole('button', { name: '查看回执 ↗', exact: true }).first();
    await fixtureTrigger.click();
    await page.locator('.receipt-result.passed').waitFor();
    assert((await page.locator('.receipt-result').textContent()).includes('当次命令通过'));
    assert.equal(await page.locator('#receipt-content img').count(), 0);
    assert.equal(await page.locator('.receipt-output pre').first().textContent(), unsafe);
    const drawerWidths = await page.locator('#source-dialog').evaluate(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
    assert(drawerWidths.scroll <= drawerWidths.client, JSON.stringify(drawerWidths));
    await page.getByRole('button', { name: '查看完整 stdout ↗' }).click();
    await page.waitForFunction(() => document.getElementById('source-content').textContent.length > 40000);
    assert.equal(await page.locator('#source-content').textContent(), unsafe.repeat(5));
    assert.equal(await page.locator('#source-dialog img').count(), 0);
    assert(await page.locator('#source-dialog').evaluate(el => el.scrollWidth <= el.clientWidth));
    await page.keyboard.press('Escape');
    assert(await fixtureTrigger.evaluate(el => el === document.activeElement));
    for (const failure of [{ inputChanged: true, error: null }, { inputChanged: false, error: '执行完整性错误' }]) {
      fixtureReceipt = { ...fixtureReceipt, summary: { ...fixtureReceipt.summary, ...failure } };
      await fixtureTrigger.click();
      await page.locator('.receipt-result.failed').waitFor();
      assert.equal(await page.locator('.receipt-result.passed').count(), 0);
      assert((await page.locator('.receipt-result').textContent()).includes('未形成有效验证 · 退出码 0'));
      await page.keyboard.press('Escape');
    }
    check('isolated-fixture-exit-zero-with-input-change-or-error-never-green');
    fixtureReceipt = { integrity: 'invalid', error: 'stdout 指纹不匹配 <img src=x>', summary: null, output: {}, rawPath: 'receipt.json' };
    await fixtureTrigger.click();
    await page.locator('.receipt-result.invalid').waitFor();
    assert.equal(await page.locator('.receipt-result.passed').count(), 0);
    assert.equal(await page.locator('.receipt-output').count(), 0);
    assert((await page.locator('#receipt-content').textContent()).includes('指纹不匹配'));
    assert.equal(await page.getByRole('button', { name: '查看原始回执（未验证，仅供取证） ↗' }).count(), 1);
    await page.keyboard.press('Escape');
    check('isolated-fixture-summary-invalid-clears-success-safe-preview-full-output-mobile-no-overflow', drawerWidths);
    failState = true;
    await page.getByRole('button', { name: '↻ 刷新事实' }).click();
    await page.locator('#error').waitFor({ state: 'visible' });
    assert((await page.locator('#error').textContent()).includes('上次成功读取的快照'));
    assert.equal(await page.locator('.task').count(), 3);
    check('isolated-fixture-refresh-failure-explicitly-labels-retained-snapshot');
    assert.deepEqual(result.pageErrors, []);
    check('no-browser-pageerror');
    result.passed = true;
  } catch (error) {
    result.passed = false; result.error = error.stack || String(error); process.exitCode = 1;
  } finally {
    if (fixtureServer) await new Promise(resolve => fixtureServer.close(resolve));
    await browser.close();
    await fs.writeFile(path.resolve(outputDir, 'evolution-ui-result.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result, null, 2));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
