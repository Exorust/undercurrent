// Performance baseline for the map page and front page.
// usage: node tools/perf.cjs <url> [--out file.json]
// PUPPETEER_CORE: path to puppeteer-core; CHROME: Chrome binary.
let puppeteer;
try { puppeteer = require(process.env.PUPPETEER_CORE || 'puppeteer-core'); }
catch { console.error('puppeteer-core not found: set PUPPETEER_CORE=/path/to/puppeteer-core'); process.exit(2); }
const fs = require('fs'), path = require('path');
const argv = process.argv.slice(2), opt = {}, pos = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i].startsWith('--')) opt[argv[i].slice(2)] = argv[++i]; else pos.push(argv[i]);
}
const url = pos[0];
if (!url || opt.help !== undefined) { console.log('Usage: node tools/perf.cjs <url> [--out file.json]'); process.exit(url ? 0 : 2); }
const W = 1600, H = 1000, sleep = ms => new Promise(r => setTimeout(r, ms));
const EXE = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const launch = () => puppeteer.launch({executablePath: EXE, headless: 'new',
  args: [`--window-size=${W},${H}`, '--enable-webgl', '--ignore-gpu-blocklist']});

async function trackPage(p) {
  const cdp = await p.createCDPSession();
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  const reqs = new Map(), t0 = {v: Date.now()}, state = {drawAt: null};
  cdp.on('Network.requestWillBeSent', e => reqs.set(e.requestId, {url: e.request.url, resourceType: e.type || null, status: null, bytes: null,
    contentEncoding: null, contentType: null, cacheControl: null, t: Date.now(), phase: null}));
  cdp.on('Network.responseReceived', e => {
    const r = reqs.get(e.requestId); if (!r) return;
    const h = Object.fromEntries(Object.entries(e.response.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    r.status = e.response.status; r.resourceType = e.type || r.resourceType;
    r.contentEncoding = h['content-encoding'] || null; r.contentType = h['content-type'] || null; r.cacheControl = h['cache-control'] || null;
  });
  cdp.on('Network.loadingFinished', e => {
    const r = reqs.get(e.requestId); if (!r) return;
    r.bytes = e.encodedDataLength; r.tEnd = Date.now();
  });
  return {reqs, state, t0};
}

const summarize = (reqs, drawAt) => {
  const list = [...reqs.values()];
  list.forEach(r => { r.phase = drawAt == null || r.t <= drawAt ? 'before' : 'after'; });
  const tot = ph => { const l = list.filter(r => r.phase === ph); return {requests: l.length, bytes: l.reduce((a, r) => a + (r.bytes || 0), 0)}; };
  return {list: list.map(({t, tEnd, ...r}) => r), before: tot('before'), after: tot('after')};
};

(async () => {
  const errors = [], out = {url};
  const b = await launch();
  try {
    // map page
    const p = await b.newPage(); await p.setViewport({width: W, height: H});
    p.on('pageerror', e => errors.push(String(e)));
    p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    const tr = await trackPage(p);
    await p.goto(url, {waitUntil: 'load', timeout: 90000}).catch(e => errors.push('goto: ' + e.message));
    let drawAt = null;
    // firstDrawMs is page-relative; detect wall time when it becomes non-null
    const start = Date.now();
    while (Date.now() - start < 60000) {
      const v = await p.evaluate('window.__radar ? window.__radar.firstDrawMs : null').catch(() => null);
      if (v != null) { drawAt = Date.now(); break; }
      await sleep(100);
    }
    if (drawAt == null) errors.push('timeout waiting for firstDrawMs');
    out.firstDrawMs = await p.evaluate('window.__radar ? window.__radar.firstDrawMs : null').catch(() => null);
    out.renderer = await p.evaluate('window.__radar ? window.__radar.renderer : null').catch(() => null);
    await sleep(5000);
    const s = summarize(tr.reqs, drawAt);
    out.requests = s.list; out.beforeFirstDraw = s.before; out.afterFirstDraw = s.after;
    // pan
    try {
      const box = await (await p.$('#map')).boundingBox(), cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      await p.mouse.move(cx, cy); await p.mouse.down(); const t = Date.now(); let k = 0;
      while (Date.now() - t < 3000) { k++; await p.mouse.move(cx + 120 * Math.sin(k / 6), cy + 60 * Math.cos(k / 6)); await sleep(16); }
      out.maxFrameMs = await p.evaluate('window.__radar.maxFrameMs'); await p.mouse.up();
    } catch (e) { out.maxFrameMs = null; errors.push('pan: ' + e.message); }
    out.filterApplyMs = await p.evaluate('window.__radar && window.__radar.filterApplyMs !== undefined ? window.__radar.filterApplyMs : null').catch(() => null);
    out.errors = errors.slice();
    await p.close();

    // front page
    const front = url.replace(/map\/?(\?.*)?$/, '');
    const fp = await b.newPage(); await fp.setViewport({width: W, height: H});
    const ft = await trackPage(fp);
    const t1 = Date.now();
    await fp.goto(front, {waitUntil: 'load', timeout: 90000}).catch(e => errors.push('front goto: ' + e.message));
    const loadMs = Date.now() - t1;
    await sleep(2000);
    const fs2 = summarize(ft.reqs, null);
    out.frontPage = {url: front, loadMs, requests: fs2.list.length, bytes: fs2.before.bytes, items: fs2.list};
    await fp.close();
  } finally { await b.close(); }
  out.errors = errors;
  const j = JSON.stringify(out, null, 1);
  if (opt.out) { fs.mkdirSync(path.dirname(path.resolve(opt.out)), {recursive: true}); fs.writeFileSync(opt.out, j); }
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({errors: [e.message]})); process.exitCode = 1; });
