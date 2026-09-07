import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const [projectRoot, sessionPath, playwrightPath, browserPath, outputDir] = process.argv.slice(2);
assert(outputDir, 'Usage: PROJECT SESSION_JSON PLAYWRIGHT_MODULE CHROME OUTPUT');
const { chromium } = createRequire(import.meta.url)(playwrightPath);
const session = JSON.parse(await fs.readFile(sessionPath, 'utf8'));
const origin = `http://127.0.0.1:${session.port}`;
const results = { checks: [], screenshots: [], pageErrors: [] };
await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ executablePath: browserPath, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', error => results.pageErrors.push(error.message));
  await page.goto(pathToFileURL(path.join(projectRoot, '东合项目进化史.html')).href);
  assert((await page.locator('#connection-state').textContent()).includes('快照'));
  assert(await page.locator('.line-card').count());
  let localRequests = 0;
  page.on('request', request => { if (request.url().startsWith('http://127.0.0.1')) localRequests++; });
  await page.locator('#refresh').click();
  assert((await page.locator('#notice').textContent()).includes('连接实时资料'));
  assert.equal(localRequests, 0);
  assert((await page.locator('#connect').getAttribute('href')).startsWith('donghe://workspace?id='));
  results.checks.push('file snapshot renders and refresh does not fetch localhost');
  await page.locator('.task summary').first().click();
  await page.locator('.task details[open] .text-link').first().click();
  assert(await page.locator('#reader').isVisible());
  assert((await page.locator('#reader-body').textContent()).length > 50);
  await page.keyboard.press('Escape');
  results.checks.push('task stream expands and opens source document');
  await page.locator('#search').fill('东合');
  assert(await page.locator('.doc-item').count());
  await page.locator('#tab-history').click();
  assert.equal(await page.locator('#tab-history').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#documents .doc-item').count(), Number(await page.locator('#history-count').textContent()));
  for (const label of await page.locator('#documents .doc-item > p:first-of-type').allTextContents()) assert(label.startsWith('历史档案'));
  results.checks.push('full-text search and historical tab work');

  const fixture = {
    project: { id: 'reader-fixture', name: '锚点与表格' }, generatedAt: new Date().toISOString(), diagnostics: [],
    lines: [{ id: 'focus-1', title: '测试任务线', status: 'unknown', sourcePath: 'docs/plan.md#focus-1', tasks: [] }, { id: 'unclassified', title: '待归类', tasks: [{ id: 'T0', title: '未知归属' }] }],
    documents: [{ path: 'docs/plan.md', title: '计划', content: '# 计划\n\n<a id="focus-1"></a>\n## 任务线\n\n| 项目 | 状态 |\n| --- | --- |\n| 源文档 | **可读** |\n| 链接 | [下节](#下一节) |\n\n## 下一节\n\n已定位。' }]
  };
  const original = await fs.readFile(path.join(projectRoot, '东合项目进化史.html'), 'utf8');
  const fixtureHTML = original.replace(/(<script\b[^>]*id=["']workspace-data["'][^>]*>)[\s\S]*?(<\/script>)/, (_, start, end) => start + JSON.stringify(fixture).replace(/</g, '\\u003c') + end);
  assert.notEqual(fixtureHTML, original);
  const fixturePath = path.resolve(outputDir, 'anchor-table-fixture.html');
  await fs.writeFile(fixturePath, fixtureHTML);
  await page.goto(pathToFileURL(fixturePath).href);
  assert.equal(await page.locator('#current-count').textContent(), '1');
  assert((await page.locator('#overview').textContent()).includes('1 个待归类任务'));
  assert.equal(await page.locator('.line-heading .badge').first().textContent(), '状态见任务');
  await page.getByRole('button', { name: '查看任务线来源', exact: true }).click();
  assert.equal(await page.locator('#reader-title').textContent(), '计划');
  assert.equal(await page.locator('#reader-body #focus-1').count(), 1);
  assert.equal(await page.locator('#reader-body table th').count(), 2);
  assert.equal(await page.locator('#reader-body table tbody tr').count(), 2);
  await page.getByRole('button', { name: '下节', exact: true }).click();
  assert.equal(await page.locator('#reader-body h2#下一节').count(), 1);
  assert((await page.locator('#reader-body').textContent()).includes('已定位'));
  await page.keyboard.press('Escape');
  results.checks.push('sourcePath anchors, explicit anchors, heading links and Markdown tables work');

  await page.goto(`${origin}/?token=${encodeURIComponent(session.token)}`);
  await page.waitForFunction(() => document.getElementById('connection-state').textContent.includes('实时资料已连接'));
  assert(await page.locator('#connect').isHidden());
  assert((await page.locator('#tab-history').textContent()).includes('历史资料'));
  const snapshotResponse = await page.request.get(`${origin}/api/snapshot`, { headers: { 'X-Donghe-Token': session.token } });
  assert.equal(snapshotResponse.status(), 200);
  const currentSnapshot = await snapshotResponse.json();
  assert.equal(Number(await page.locator('#current-count').textContent()), currentSnapshot.lines.filter(line => line.id !== 'unclassified').length);
  assert.equal(Number(await page.locator('#history-count').textContent()), currentSnapshot.documents.filter(doc => doc.archived).length);
  for (const label of await page.locator('.line-heading .badge').allTextContents()) assert.notEqual(label, '状态未明');
  const initialLines = await page.locator('.line-card').count();
  assert(initialLines);
  for (const operation of ['refresh', 'reindex']) {
    const response = page.waitForResponse(response => response.url() === `${origin}/api/${operation}`);
    await page.locator('#' + operation).click();
    assert.equal((await response).status(), 200);
    await page.waitForFunction(() => !document.getElementById('refresh').disabled);
    assert((await page.locator('#notice').textContent()).includes('已读取落盘资料'));
  }
  results.checks.push('authenticated HTTP snapshot, refresh and reindex succeed');
  await page.route('**/api/refresh', route => route.abort('failed'));
  await page.locator('#refresh').click();
  await page.waitForFunction(() => document.getElementById('notice').textContent.includes('更新未完成'));
  assert.equal(await page.locator('.line-card').count(), initialLines);
  assert(await page.locator('#connect').isVisible());
  await page.unroute('**/api/refresh');
  results.checks.push('failed refresh preserves last successful snapshot');
  const recoveredResponse = page.waitForResponse(response => response.url() === `${origin}/api/refresh`);
  await page.locator('#refresh').click();
  assert.equal((await recoveredResponse).status(), 200);
  await page.waitForFunction(() => document.getElementById('connection-state').textContent === '实时资料已连接' && !document.getElementById('refresh').disabled);
  assert((await page.locator('#notice').textContent()).includes('已读取落盘资料'));
  const desktop = path.resolve(outputDir, 'workspace-desktop.png');
  assert(await page.locator('#connect').isHidden());
  await page.screenshot({ path: desktop, fullPage: true }); results.screenshots.push(desktop);
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(element => element.getBoundingClientRect().right > innerWidth + 1).map(element => ({ tag: element.tagName, id: element.id, class: element.className, right: element.getBoundingClientRect().right })));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), JSON.stringify(overflow));
  await page.locator('.task summary').first().click();
  await page.locator('.task details[open] .text-link').first().click();
  assert(await page.locator('#reader').evaluate(element => element.getBoundingClientRect().right <= innerWidth));
  const mobile = path.resolve(outputDir, 'workspace-mobile-source.png');
  await page.screenshot({ path: mobile }); results.screenshots.push(mobile);
  results.checks.push('390px layout and document dialog have no horizontal overflow');
  assert.deepEqual(results.pageErrors, []);
  results.passed = true;
} catch (error) {
  results.passed = false; results.failure = error.stack; process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile(path.join(outputDir, 'result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
}
