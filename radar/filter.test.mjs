import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const F = require('./filter.js');

const E = { inst: [], country: null, venue: false, code: false, cit: null };
const mk = (o) => Object.assign({}, E, o);

// 20-point fixture
const n = 20;
const flags = new Uint8Array(n);
const citpct = new Uint8Array(n);
for (let i = 0; i < n; i++) citpct[i] = i * 5; // 0..95
flags[1] = 1; flags[2] = 2; flags[3] = 4; flags[4] = 8; flags[5] = 1 | 8;
flags[6] = 2 | 8; flags[7] = 16; flags[10] = 1; flags[11] = 8; flags[12] = 1 | 8;
citpct[13] = 255; flags[13] = 1 | 8;
const instRows = [Uint32Array.from([1, 2, 4, 5]), Uint32Array.from([5, 6, 12, 13])];
const countryRows = Uint32Array.from([2, 5, 6, 10, 12, 13, 19]);
const data = { flags, citpct, instRows, countryRows };
const on = (m) => Array.from(m).map((v, i) => v ? i : -1).filter((x) => x >= 0);

assert.equal(F.buildMask(data, mk({})), null);
assert.equal(F.isActive(mk({})), false);
assert.deepEqual(on(F.buildMask({ ...data, instRows: [instRows[0]] }, mk({ inst: ['I1'] }))), [1, 2, 4, 5]);
assert.deepEqual(on(F.buildMask(data, mk({ inst: ['I1', 'I2'] }))), [1, 2, 4, 5, 6, 12, 13]);
assert.deepEqual(on(F.buildMask(data, mk({ country: 'US' }))), [2, 5, 6, 10, 12, 13, 19]);
assert.deepEqual(on(F.buildMask(data, mk({ country: 'US' }), undefined)), [2, 5, 6, 10, 12, 13, 19]);
assert.deepEqual(on(F.buildMask({ ...data, countryRows: null }, mk({ country: 'US' }))), []);
assert.deepEqual(on(F.buildMask(data, mk({ venue: true }))), [1, 2, 5, 6, 10, 12, 13]);
assert.deepEqual(on(F.buildMask(data, mk({ code: true }))), [4, 5, 6, 11, 12, 13]);
assert.deepEqual(on(F.buildMask(data, mk({ cit: 10 }))), [18, 19]); // >=90
assert.deepEqual(on(F.buildMask(data, mk({ cit: 25 }))), [15, 16, 17, 18, 19]); // >=75
assert.equal(on(F.buildMask(data, mk({ cit: 50 }))).includes(13), false); // unknown never passes
assert.equal(on(F.buildMask(data, mk({ cit: 50 }))).length, 9); // 50..95 minus unknown row 13
// combinations
assert.deepEqual(on(F.buildMask(data, mk({ inst: ['I1', 'I2'], country: 'US' }))), [2, 5, 6, 12, 13]);
assert.deepEqual(on(F.buildMask(data, mk({ inst: ['I1', 'I2'], country: 'US', venue: true, code: true }))), [5, 6, 12, 13]);
assert.deepEqual(on(F.buildMask(data, mk({ inst: ['I1', 'I2'], country: 'US', venue: true, code: true, cit: 50 }))), [12]);
// out buffer reuse
const buf = new Uint8Array(n).fill(1);
const m2 = F.buildMask({ ...data, instRows: [instRows[0]] }, mk({ inst: ['I1'] }), buf);
assert.equal(m2, buf);
assert.deepEqual(on(m2), [1, 2, 4, 5]);
const m3 = F.buildMask(data, mk({ venue: true }), buf);
assert.deepEqual(on(m3), [1, 2, 5, 6, 10, 12, 13]);

// countByTopic
const labels = new Int16Array(n);
for (let i = 0; i < n; i++) labels[i] = i % 4 === 3 ? -1 : i % 3;
const cnt = F.countByTopic(F.buildMask(data, mk({ venue: true })), labels, 3);
const exp = [0, 0, 0];
for (const i of [1, 2, 5, 6, 10, 12, 13]) if (labels[i] >= 0) exp[labels[i]]++;
assert.deepEqual(Array.from(cnt), exp);
const all = F.countByTopic(null, labels, 3);
assert.equal(all[0] + all[1] + all[2], labels.filter((l) => l >= 0).length);

// query
const q = F.toQuery(mk({ inst: ['I1', 'I22'], country: 'US', venue: true, code: true, cit: 10 }));
assert.equal(q, 'inst=I1,I22&country=US&venue=1&code=1&cit=10');
assert.deepEqual(F.fromQuery(q), mk({ inst: ['I1', 'I22'], country: 'US', venue: true, code: true, cit: 10 }));
assert.equal(F.toQuery(mk({})), '');
assert.deepEqual(F.fromQuery(''), mk({}));
assert.deepEqual(F.fromQuery('inst=I1,x2,I3,I4,I5,I6,I7&country=usa&cit=33&venue=0').inst, ['I1', 'I3', 'I4', 'I5', 'I6']);
const bad = F.fromQuery('inst=foo&country=usa&cit=33&venue=0&code=2');
assert.deepEqual(bad, mk({}));
assert.equal(F.fromQuery('?cit=25&country=DE').cit, 25);

// performance
const N = 459249;
const pf = new Uint8Array(N), pc = new Uint8Array(N), lab = new Int16Array(N);
let s = 12345;
const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
for (let i = 0; i < N; i++) {
  pf[i] = Math.floor(rnd() * 32);
  pc[i] = rnd() < 0.1 ? 255 : Math.floor(rnd() * 101);
  lab[i] = rnd() < 0.05 ? -1 : Math.floor(rnd() * 114);
}
const pick = (cnt) => { const set = new Set(); while (set.size < cnt) set.add(Math.floor(rnd() * N)); return Uint32Array.from(set).sort(); };
const pdata = { flags: pf, citpct: pc, instRows: [pick(5000), pick(5000), pick(5000)], countryRows: pick(80000) };
const pfilt = mk({ inst: ['I1', 'I2', 'I3'], country: 'US', venue: true, code: true, cit: 50 });
const pfilt2 = mk({ venue: true, code: true, cit: 25 });
const pout = new Uint8Array(N);
const run = (ff) => { const t = performance.now(); const m = F.buildMask(pdata, ff, pout); F.countByTopic(m, lab, 114); return performance.now() - t; };
for (const [name, ff] of [['inst+country+flags', pfilt], ['flags only', pfilt2]]) {
  run(ff);
  const ts = []; for (let r = 0; r < 5; r++) ts.push(run(ff));
  ts.sort((a, b) => a - b);
  console.log(`perf ${name}: median ${ts[2].toFixed(1)} ms`);
  assert.ok(ts[2] < 60, `too slow: ${ts[2]}`);
}
console.log('ok');
