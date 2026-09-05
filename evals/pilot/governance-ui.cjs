const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require('/Users/aiden/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const [url,root,out]=process.argv.slice(2);await fs.mkdir(out,{recursive:true});
 const cli=path.resolve(__dirname,'../../scripts/donghe.py');
 const call=(...args)=>JSON.parse(execFileSync('python3',[cli,'--project',root,...args],{encoding:'utf8'}));
 const browser=await chromium.launch({executablePath:'/Users/aiden/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',headless:true});
 const results=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('#relations-panel').waitFor({state:'visible'});
  assert.equal(await page.locator('.record-card').count(),3);
  assert((await page.locator('[data-record-id="I001"]').textContent()).includes('GOV001'));
  assert((await page.locator('[data-record-id="B001"]').textContent()).includes('合成想法'));
  await page.screenshot({path:path.join(out,'relations-before.png'),fullPage:true});
  const logical='docs/东合/资料/knowledge/K001.md';
  const before=await fs.readFile(path.join(root,logical));
  const preview=call('archive','--month','2026-08');assert.equal(preview.count,1);
  assert.deepEqual(await fs.readFile(path.join(root,logical)),before);
  call('archive','--month','2026-08','--apply');
  await page.getByRole('button',{name:'↻ 刷新事实'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-record-id="K001"]').textContent.includes('已归档'));
  await page.getByLabel('筛选资料类型').selectOption('knowledge');assert.equal(await page.locator('.record-card').count(),1);
  await page.locator('[data-record-id="K001"]').getByRole('button',{name:'资料原文 ↗'}).click();
  await page.waitForFunction(()=>document.getElementById('source-content').textContent.includes('合成历史'));
  assert.equal(await page.locator('#source-content').textContent(),before.toString());await page.keyboard.press('Escape');
  await page.getByLabel('筛选资料类型').selectOption('');
  await page.screenshot({path:path.join(out,'relations-archived.png'),fullPage:true});
  const cold=call('catalog');assert(cold.records.find(r=>r.id==='K001').archived);
  assert.equal(cold.issues.length,0);
  // Browser receipt still reads correctly after another source is moved.
  await page.getByRole('button',{name:'查看回执 ↗',exact:true}).first().click();await page.locator('.receipt-result').waitFor();
  assert((await page.locator('.receipt-result').textContent()).includes('退出码 0'));await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,'relations-mobile.png'),fullPage:true});
  call('archive','--month','2026-08','--restore');assert.deepEqual(await fs.readFile(path.join(root,logical)),before);
  assert.equal(call('next').action,'stop');assert.equal(errors.length,0);
  results.push({passed:true,checks:['explicit idea-direction-task links','upstream feedback','preview is read-only','archive bytes preserved','archived original source via browser','type filter','cold catalog','receipt still accessible','390px no overflow','restore bytes exact','no new work','no pageerror']});
 }catch(e){results.push({passed:false,error:e.stack});process.exitCode=1;}
 finally{await browser.close();await fs.writeFile(path.join(out,'result.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));}
})();
