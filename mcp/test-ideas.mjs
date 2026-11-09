import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

// The data module only exists in an export. Substitute a fixture in memory;
// keep the production module and Blob implementation otherwise unchanged.
const fixture = [{ ideas: [{ text: 'PRIVATE IDEA TEXT' }] }];
const source = readFileSync(new URL('./api/ideas.js', import.meta.url), 'utf8')
  .replace("from '@vercel/blob'", `from '${import.meta.resolve('@vercel/blob')}'`)
  .replace("import IDEAS from './_ideas-data.mjs';", `const IDEAS = ${JSON.stringify(fixture)};`);
const { signToken, verifyToken, validEmail, createHandler } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const secret = 'offline-test-secret', env = { UC_TOKEN_SECRET: secret };
const token = signToken('e', 'Jane@Example.com', secret);
assert.deepEqual(verifyToken(token, secret), { kind: 'e', subject: 'jane@example.com' });
assert.equal(verifyToken(token, 'wrong-secret'), null);
assert.equal(verifyToken(token, ''), null);
assert.throws(() => signToken('i', 'jane', ''));
assert.equal(verifyToken(token, secret, 'other, jane@example.com'), null);
assert.equal(verifyToken(token.replace('v1.e.', 'v1.i.'), secret), null);
const parts = token.split('.');
parts[2] = Buffer.from('JANE@example.com').toString('base64url');
assert.equal(verifyToken(parts.join('.'), secret), null);
for (const bad of ['', 'v2.e.x.x', `${token}.extra`, token.slice(0, -1), token.slice(0, -2) + 'aa']) assert.equal(verifyToken(bad, secret), null);
for (const bad of [null, 12, {}, '', 'not-an-email', 'a@@b.com', 'a b@c.com', 'a@b', 'x'.repeat(249) + '@x.com']) assert.equal(validEmail(bad), false);
assert.equal(validEmail('x'.repeat(248) + '@x.com'), true);
assert.equal(validEmail('Jane+ideas@example.com'), true);

let time = 1_700_000_000_000, writes = 0;
const records = new Map();
const store = { read: async path => records.get(path), write: async (path, value) => { writes++; records.set(path, value); } };
const handler = createHandler({ store, env, now: () => time });
async function request(fn, method = 'GET', body, auth, ip = 'test') {
  const headers = {}, response = { headers };
  await fn({ method, body, headers: { authorization: auth ? `Bearer ${auth}` : undefined, 'x-forwarded-for': ip } }, {
    setHeader: (k, v) => { headers[k] = v; },
    writeHead: (status, h) => { response.status = status; Object.assign(headers, h); },
    end: raw => { response.raw = raw; response.body = JSON.parse(raw); }
  });
  return response;
}
let r = await request(handler);
assert.equal(r.status, 401);
assert.deepEqual(r.body, { error: 'auth', count: 1 });
assert.equal(r.headers['Cache-Control'], 'no-store');
assert.ok(!r.raw.includes('PRIVATE IDEA TEXT'));
assert.equal((await request(handler, 'GET', undefined, token.slice(0, -1))).status, 401);
assert.equal((await request(handler, 'GET', undefined, signToken('e', 'a@b.com', 'wrong'))).status, 401);
assert.equal((await request(handler, 'POST', { email: 'not-an-email' })).status, 400);
assert.equal((await request(handler, 'POST', '{bad')).status, 400);
assert.equal(writes, 0);
r = await request(handler, 'POST', { email: 'Jane@Example.com' });
assert.equal(r.status, 200);
assert.equal(r.body.token, token);
const signup = `signups/${createHash('sha256').update('jane@example.com').digest('hex')}.json`;
assert.deepEqual(records.get(signup), { email: 'jane@example.com', ts: new Date(time).toISOString() });
assert.equal((await request(handler, 'POST', JSON.stringify({ email: 'jane@example.com' }))).status, 200);
assert.equal(records.size, 1);
r = await request(handler, 'GET', undefined, token);
assert.equal(r.status, 200);
assert.deepEqual(r.body, fixture);
assert.equal(r.headers['Cache-Control'], 'private, no-store');
env.UC_REVOKED = 'jane@example.com';
assert.equal((await request(handler, 'GET', undefined, token)).status, 401);
delete env.UC_REVOKED;
for (const method of ['PUT', 'DELETE', 'OPTIONS']) assert.equal((await request(handler, method)).status, 405);
const closed = createHandler({ store, env: {} });
for (const method of ['GET', 'POST']) {
  r = await request(closed, method, { email: 'a@b.com' }, token);
  assert.equal(r.status, 500); assert.ok(!r.body.token);
}
const failed = createHandler({ env, store: { read: async () => { throw new Error('offline'); }, write: async () => { throw new Error('offline'); } } });
r = await request(failed, 'POST', { email: 'a@b.com' });
assert.equal(r.status, 503); assert.ok(!r.body.token);
const limited = createHandler({ store, env, now: () => time });
for (let i = 0; i < 10; i++) assert.equal((await request(limited, 'POST', {})).status, 400);
r = await request(limited, 'POST', {});
assert.equal(r.status, 429); assert.equal(r.headers['Retry-After'], '600');
assert.equal((await request(limited, 'POST', {}, undefined, 'other')).status, 400);
time += 600_000;
assert.equal((await request(limited, 'POST', {})).status, 400);

const url = execFileSync('python3', [new URL('../tools/invite.py', import.meta.url).pathname, 'Jane Doe'], {
  env: { PATH: process.env.PATH, UC_TOKEN_SECRET: secret }, encoding: 'utf8'
}).trim();
assert.ok(url.startsWith('https://charoori.com/undercurrent/map/?invite='));
const invite = new URL(url).searchParams.get('invite');
assert.deepEqual(verifyToken(invite, secret), { kind: 'i', subject: 'jane-doe' });
assert.equal((await request(handler, 'GET', undefined, invite)).status, 200);
const firstSeen = new Date(time).toISOString();
assert.deepEqual(records.get('invites/jane-doe.json'), { firstSeen, lastSeen: firstSeen });
time += 1000;
assert.equal((await request(handler, 'GET', undefined, invite)).status, 200);
assert.deepEqual(records.get('invites/jane-doe.json'), { firstSeen, lastSeen: new Date(time).toISOString() });
assert.equal((await request(failed, 'GET', undefined, invite)).status, 200);
env.UC_REVOKED = 'jane-doe';
assert.equal((await request(handler, 'GET', undefined, invite)).status, 401);
console.log('ideas: all offline checks passed');
