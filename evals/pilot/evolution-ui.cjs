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
    await page.waitForFunction(() => document.getElementById('source-content').textContent.includes('exitCode'));
    const receiptData = JSON.parse(await page.locator('#source-content').textContent());
    assert.equal(typeof receiptData.exitCode, 'number');
    await page.getByRole('button', { name: '关闭原始记录' }).click();
    assert(await receipt.evaluate(el => el === document.activeElement), 'Source trigger regains focus');
    check('real-receipt-opens-and-close-restores-focus', { exitCode: receiptData.exitCode });

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
    await page.waitForFunction(() => document.getElementById('source-content').textContent.includes('exitCode'));
    assert(await page.locator('#source-dialog').evaluate(el => el.getBoundingClientRect().right <= innerWidth));
    await page.keyboard.press('Escape');
    const mobile = path.resolve(outputDir, 'evolution-mobile.png');
    await page.screenshot({ path: mobile, fullPage: true }); result.screenshots.push(mobile);
    check('mobile-390px-no-horizontal-overflow-and-source-drawer', widths);

    // A separate ephemeral service exercises counterexamples without modifying pilot data.
    const html = await fs.readFile(path.resolve(__dirname, '../../assets/evolution.html'));
    let fixtureState = { projectRoot: '/fixture/empty-project', generatedAt: '2026-09-05T00:00:00Z', decision: { action: 'stop', reason: 'Fixture: no authorized task' }, tasks: [], handoffPath: null, logPaths: [] };
    let failState = false;
    fixtureServer = http.createServer((req, res) => {
      if (req.url === '/api/state') { res.writeHead(failState ? 503 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(fixtureState)); }
      else { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); }
    });
    await new Promise(resolve => fixtureServer.listen(0, '127.0.0.1', resolve));
    await page.goto('http://127.0.0.1:' + fixtureServer.address().port);
    await page.locator('.empty').waitFor();
    assert((await page.locator('.empty').textContent()).includes('当前已停止'));
    assert.equal(await page.locator('#completed-count').textContent(), '0');
    check('isolated-fixture-empty-is-stopped-not-complete');
    fixtureState = { ...fixtureState, decision: { action: 'recover', reason: 'Fixture: evidence needs repair' }, tasks: ['pending', 'stale', 'invalid'].map((verification, i) => ({ id: 'FIX' + i, title: i === 0 ? '<img src=x onerror=alert(1)>' : verification + ' fixture', status: 'completed', verification, issues: ['Fixture blocker'], criteria: [{ id: 'C1', label: 'Fixture criterion', state: i === 0 ? 'failed' : verification, receiptPath: null }], events: [{ at: '2026-09-05T00:00:00Z', label: 'Fixture failure preserved', kind: 'failed' }] })) };
    await page.getByRole('button', { name: '↻ 刷新事实' }).click();
    await page.locator('.task').first().waitFor();
    assert.equal(await page.locator('#completed-count').textContent(), '0');
    for (const text of ['待验证', '证据已陈旧', '证据无效', '验证失败']) assert((await page.locator('#journey').textContent()).includes(text));
    assert.equal(await page.locator('.task img').count(), 0);
    assert((await page.locator('.task h3').first().textContent()).includes('<img'));
    check('isolated-fixture-failed-stale-invalid-never-count-as-passed-and-safe-text');
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
