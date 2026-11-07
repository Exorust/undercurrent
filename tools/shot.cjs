// Headless Chrome check for radar/app.html (arch §8.3, F35.2).
// usage: node tools/shot.cjs [url] out.png [--preset connect] [--click-topic ID] [--viewport WxH]
//        [--eval "<js>"] [--wait MS] [--reduced-motion] [--no-measure]
// Prints one JSON line: firstDrawMs, lastRecolorMs, maxFrameMs (scripted 2 s pan), heapMB, renderer, errors, eval.
// PUPPETEER_CORE: path to a puppeteer-core install (npm i puppeteer-core); CHROME: the Chrome binary.
let puppeteer;
try { puppeteer = require(process.env.PUPPETEER_CORE || 'puppeteer-core'); }
catch { console.error('puppeteer-core not found: npm i -g puppeteer-core, or set PUPPETEER_CORE=/path/to/puppeteer-core'); process.exit(2); }
if (process.argv.includes('--help')) {
  console.log('Usage: node tools/shot.cjs [url] [out.png] [--preset connect] [--click-topic ID] [--viewport WxH] [--eval "JS expression or statements with return"] [--wait MS] [--reduced-motion] [--no-measure]');
  process.exit(0);
}
const argv = process.argv.slice(2), opt = {}, pos = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--reduced-motion' || a === '--no-measure') opt[a.slice(2)] = true;
  else if (a.startsWith('--')) opt[a.slice(2)] = argv[++i];
  else pos.push(a);
}
const url = pos.find(p => /^https?:|^file:/.test(p)) || 'http://localhost:8765';
const out = pos.find(p => p.endsWith('.png'));
const [W, H] = (opt.viewport || '1600x1000').split('x').map(Number);
const sleep = ms => new Promise(r => setTimeout(r, ms));
if (out) require('fs').mkdirSync(require('path').dirname(require('path').resolve(out)), {recursive: true});

(async () => {
  const b = await puppeteer.launch({executablePath: process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: 'new',
    args: [`--window-size=${W},${H}`, '--enable-webgl', '--ignore-gpu-blocklist']});
  const errors = [], res = {};
  try {
    const p = await b.newPage(); await p.setViewport({width: W, height: H});
    if (opt['reduced-motion']) await p.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
    p.on('pageerror', e => errors.push(String(e)));
    p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await p.goto(url, {waitUntil: 'load'});
    await p.waitForFunction('window.__radar && window.__radar.ready', {timeout: 60000}).catch(() => errors.push('timeout waiting for __radar.ready'));
    await sleep(800);
    if (opt.preset === 'connect') { await p.keyboard.press('2'); await sleep(1200); }
    if (opt['click-topic'] != null) {
      await p.click(`#pTopics tr[data-c="${opt['click-topic']}"] td:nth-child(2)`).catch(e => errors.push('click-topic: ' + e.message));
      await p.waitForFunction('(() => { const s = window.__radar.selection(); return s.A && s.A.sel_id; })()', {timeout: 20000}).catch(() => errors.push('timeout waiting for selection'));
      await sleep(1000);
    }
    if (opt.wait) await sleep(+opt.wait);
    if (opt.eval) {
      // Compile before execution: a runtime SyntaxError must never run the input twice.
      let source;
      try { new Function(`return (async () => (${opt.eval}))()`); source = `(async () => (${opt.eval}))()`; }
      catch { source = `(async () => {${opt.eval}\n})()`; }
      try { res.eval = (await p.evaluate(source)) ?? null; }
      catch (e) { res.eval = {error: e.message}; errors.push('eval: ' + e.message); }
    }
    if (out) await p.screenshot({path: out});
    if (!opt['no-measure']) {
      // scripted 2 s pan on the canvas, then one colour change and back for lastRecolorMs
      const box = await (await p.$('#map')).boundingBox(), cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      await p.mouse.move(cx, cy); await p.mouse.down(); const t0 = Date.now(); let k = 0;
      while (Date.now() - t0 < 2000) { k++; await p.mouse.move(cx + 120 * Math.sin(k / 6), cy + 60 * Math.cos(k / 6)); await sleep(16); }
      res.maxFrameMs = await p.evaluate('window.__radar.maxFrameMs'); await p.mouse.up();
      await p.evaluate(`document.querySelector('input[name=colour][value=date]').click()`); await sleep(600);
      await p.evaluate(`document.querySelector('input[name=colour][value=growth]').click()`); await sleep(600);
    }
    Object.assign(res, await p.evaluate(`({firstDrawMs: __radar.firstDrawMs, firstRecolorMs: __radar.firstRecolorMs, lastRecolorMs: __radar.lastRecolorMs, nPoints: __radar.nPoints, renderer: __radar.renderer})`));
    const m = await p.metrics(); res.heapMB = Math.round(m.JSHeapUsedSize / 1e6);
  } finally { await b.close(); }
  res.errors = errors; res.screenshot = out || null;
  console.log(JSON.stringify(res));
  process.exit(errors.length ? 1 : 0);
})().catch(e => { console.log(JSON.stringify({errors: [e.message], screenshot: out || null})); process.exitCode = 1; });
