// Actual browser acceptance of the existing project's brand and source-reader path.
const { chromium } = require(process.argv[2]);
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const executablePath = process.argv[3], root = process.cwd();
  const cli = path.join(root, 'tools/project-chronicle/dist/cli.js');
  execFileSync(process.execPath, [cli, 'refresh'], {cwd:root, stdio:'pipe'});
  const server = spawn(process.execPath, ['--input-type=module', '-e',
    `import {createReaderServer} from './tools/project-chronicle/dist/server.js';
     import {loadConfig} from './tools/project-chronicle/dist/repository.js';
     const s=createReaderServer(process.cwd(),process.cwd()+'/chronicle',loadConfig(process.cwd()+'/tools/project-chronicle'));
     s.listen(0,'127.0.0.1',()=>console.log(s.address().port));`], {cwd:root,stdio:['ignore','pipe','pipe']});
  let browser;
  try {
    const port = await new Promise((resolve,reject) => {let buffer=''; const timer=setTimeout(()=>reject(Error('server timeout')),20000);server.stdout.on('data',d=>{buffer+=d;if(buffer.includes('\n')){clearTimeout(timer);resolve(Number(buffer.trim()));}});server.once('error',reject);});
    browser = await chromium.launch({headless:true,executablePath});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:'+port);
    assert.equal(await page.title(),'项目进展 · 东合项目进化史');
    assert.equal(await page.locator('.brand').textContent(),'东合项目进化史');
    assert.match(await page.locator('.kicker').textContent(),/回光 \/ BacklitOS/);
    await page.locator('#results .card h2 a').first().click();
    await page.locator('#reader[open] #reading h1').first().waitFor();
    assert.ok((await page.locator('#reading').textContent()).length>50);
    await page.locator('#reader-close').click();
    assert.equal(await page.locator('#reader').evaluate(e=>e.open),false);
    fs.mkdirSync('output/playwright',{recursive:true});
    await page.screenshot({path:'output/playwright/chronicle-brand.png',fullPage:false});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['fixed brand','separate project identity','real source opens','reader closes','no page errors'],screenshot:'output/playwright/chronicle-brand.png'}));
  } finally {if(browser) await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
