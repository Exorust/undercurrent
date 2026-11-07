import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Readable } from 'node:stream';
import { EventEmitter } from 'node:events';
import handler from './api/mcp.js';

const root = new URL('../site/data/', import.meta.url);
const base = (process.env.UC_DATA_BASE || 'https://undercurrent-five-kappa.vercel.app/data').replace(/\/$/, '');
const calls = []; let failArxiv = false;
const atom = `<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2403.00813v1</id><title>UrbanGPT: Spatio-Temporal\n Models &amp; Evidence</title>
<author><name>Ada Example</name></author><author><name>Bo Test</name></author>
<summary>Urban forecasting with models.&#10; Tested offline.</summary><published>2024-03-01T00:00:00Z</published>
</entry></feed>`;
const originalFetch = globalThis.fetch;
globalThis.fetch = async (input, options) => {
  const url = String(input); calls.push(url);
  if (url.startsWith('https://export.arxiv.org/api/query?')) {
    assert.equal(options.headers['User-Agent'], 'undercurrent-mcp/0.1 (+https://charoori.com/undercurrent/)');
    assert.ok(options.signal);
    if (failArxiv) throw new Error('offline failure');
    return new Response(atom, { headers: { 'Content-Type': 'application/atom+xml' } });
  }
  assert.ok(url.startsWith(base + '/'), `Unexpected network request: ${url}`);
  try { return new Response(await readFile(new URL(url.slice(base.length + 1), root))); }
  catch (e) { if (e.code === 'ENOENT') return new Response('', { status: 404 }); throw e; }
};
let sequence = 0;
async function request(method, params, verb = 'POST') {
  const body = { jsonrpc: '2.0', id: ++sequence, method, params };
  const req = Readable.from([JSON.stringify(body)]);
  Object.assign(req, { method: verb, url: '/api/mcp', body, headers: {
    host: 'localhost', 'content-type': 'application/json', accept: 'application/json, text/event-stream'
  } });
  req.rawHeaders = Object.entries(req.headers).flat();
  req.socket = {};
  const res = new EventEmitter(); let output = ''; const headers = {};
  res.setHeader = (key, value) => { headers[key.toLowerCase()] = value; };
  res.getHeader = key => headers[key.toLowerCase()];
  res.writeHead = (status, extra = {}) => { res.statusCode = status; Object.entries(extra).forEach(([k, v]) => res.setHeader(k, v)); res.headersSent = true; return res; };
  res.write = chunk => { output += typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString(); return true; };
  res.end = chunk => { if (chunk) output += typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString(); res.writableEnded = true; res.emit('finish'); res.emit('close'); return res; };
  await handler(req, res);
  assert.equal(headers['access-control-allow-origin'], '*');
  return { status: res.statusCode, headers, body: output ? JSON.parse(output) : null };
}
async function rpc(method, params) {
  const r = await request(method, params);
  assert.equal(r.status, 200, JSON.stringify(r.body)); assert.ok(!r.body.error, JSON.stringify(r.body));
  assert.ok(!r.headers['mcp-session-id']); return r.body.result;
}
async function call(name, args) {
  const result = await rpc('tools/call', { name, arguments: args });
  assert.ok(!result.isError, JSON.stringify(result)); return JSON.parse(result.content[0].text);
}
const ok = step => console.log(`ok ${step}`);
try {
  const init = await rpc('initialize', { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'offline-test', version: '1' } });
  assert.equal(init.serverInfo.name, 'undercurrent'); ok('initialize');
  assert.equal((await rpc('tools/list', {})).tools.length, 7); ok('tools/list');
  assert.equal((await rpc('prompts/list', {})).prompts.length, 1); ok('prompts/list');
  const topics = await call('list_topics', { level: 1, limit: 200 });
  assert.equal(topics.length, 114); assert.ok(topics.every(t => Number.isInteger(t.papers))); ok('list_topics: 114 topics');
  const topic = await call('get_topic', { level: 1, id: 63 });
  assert.equal(topic.label, 'Urban Mobility and Spatiotemporal Forecasting');
  assert.ok(topic.top_cited.every(p => p.title && p.url)); ok('get_topic: Urban Mobility and Spatiotemporal Forecasting');
  assert.ok((await call('arriving', {})).length >= 10); ok('arriving');
  const links = await call('links', { topic_id: 63 });
  assert.ok(links.length && links.every(p => p.c1 === 63 || p.c2 === 63)); ok('links: topic filter');
  const pair = await call('compare', { a: { level: 1, id: 63 }, b: { level: 1, id: 75 } });
  assert.ok(Number.isFinite(pair.cosine_distance) && pair.cosine_distance >= 0 && pair.cosine_distance <= 1);
  assert.equal(pair.link.n_bridge, 38); assert.ok(pair.bridge_papers.length); assert.ok(pair.arxiv_ids.includes(pair.bridge_papers[0].id));
  ok('compare: distance and bridge papers');
  const live = await call('get_papers', { ids: ['2403.00813'] });
  assert.equal(live[0].title, 'UrbanGPT: Spatio-Temporal Models & Evidence');
  assert.deepEqual(live[0].authors, ['Ada Example', 'Bo Test']); assert.equal(live[0].abstract, 'Urban forecasting with models. Tested offline.'); ok('get_papers: Atom');
  const novelty = await call('novelty_check', { idea: 'Traffic simulation', key_terms: ['traffic prediction', 'simulation'] });
  assert.equal(novelty.query, 'all:"traffic prediction" AND all:"simulation"'); assert.equal(novelty.papers.length, 1);
  const extracted = await call('novelty_check', { idea: 'Forecasting transportation with simulation' });
  assert.ok(extracted.query.startsWith('all:"transportation"')); ok('novelty_check');
  const prompt = await rpc('prompts/get', { name: 'ideas_between', arguments: { a_label: 'Mobility', b_label: 'Driving' } });
  assert.equal(prompt.messages.length, 1); assert.match(prompt.messages[0].content.text, /Never cite an ID/); ok('ideas_between');
  await call('compare', { a: { level: 0, id: 218 }, b: { level: 2, id: 12 } });
  const noBridge = await call('compare', { a: { level: 1, id: 63 }, b: { level: 1, id: 63 } });
  assert.equal(noBridge.bridge_note, 'no bridge-paper file for this pair in the snapshot');
  assert.ok(Math.abs(noBridge.cosine_distance) < 1e-12);
  assert.equal(calls.filter(u => u.endsWith('centroids-0.bin')).length, 1);
  assert.equal(calls.filter(u => u.endsWith('topics-1-6m.json')).length, 1); ok('centroids: all levels, missing pair, warm cache');
  failArxiv = true;
  const failed = await rpc('tools/call', { name: 'get_papers', arguments: { ids: ['2403.00813'] } });
  assert.equal(failed.isError, true); assert.match(failed.content[0].text, /arXiv API unavailable/);
  const invalid = await rpc('tools/call', { name: 'list_topics', arguments: { level: 9 } });
  assert.equal(invalid.isError, true); ok('API failure and schema validation');
  for (const verb of ['GET', 'DELETE']) assert.equal((await request('', {}, verb)).status, 405);
  assert.equal((await request('', {}, 'OPTIONS')).status, 204); ok('HTTP methods and CORS');
} catch (e) { console.error(e); process.exitCode = 1; }
finally { globalThis.fetch = originalFetch; }
