import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import { get, put } from '@vercel/blob';
import IDEAS from './_ideas-data.mjs';

export function validEmail(email) {
  return typeof email === 'string' && email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}
export function signToken(kind, subject, secret) {
  if (!secret) throw new Error('Token secret unavailable');
  if (kind === 'e') subject = subject.toLowerCase();
  if (!(kind === 'e' ? validEmail(subject) : kind === 'i' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(subject))) throw new Error('Invalid subject');
  const payload = `v1.${kind}.${Buffer.from(subject).toString('base64url')}`;
  return `${payload}.${createHmac('sha256', secret).update(payload).digest('base64url')}`;
}
export function verifyToken(token, secret, revoked = '') {
  if (!secret || typeof token !== 'string' || token.length > 2048) return null;
  const parts = token.split('.');
  if (parts.length !== 4 || parts[0] !== 'v1' || !/^[ei]$/.test(parts[1]) || !/^[A-Za-z0-9_-]+$/.test(parts[2]) || !/^[A-Za-z0-9_-]{43}$/.test(parts[3])) return null;
  const subject = Buffer.from(parts[2], 'base64url').toString('utf8');
  if (Buffer.from(subject).toString('base64url') !== parts[2] || (parts[1] === 'e' && subject !== subject.toLowerCase())) return null;
  try {
    const expected = signToken(parts[1], subject, secret).split('.')[3];
    const signature = Buffer.from(parts[3], 'base64url');
    if (signature.toString('base64url') !== parts[3] || !timingSafeEqual(Buffer.from(expected, 'base64url'), signature)) return null;
  } catch { return null; }
  if (revoked.split(',').map(s => s.trim().toLowerCase()).includes(subject.toLowerCase())) return null;
  return { kind: parts[1], subject };
}
const blobStore = {
  async read(path) {
    const result = await get(path, { access: 'private', useCache: false });
    return result?.statusCode === 200 ? new Response(result.stream).json() : null;
  },
  async write(path, value) {
    await put(path, JSON.stringify(value), { access: 'private', addRandomSuffix: false, allowOverwrite: true, contentType: 'application/json' });
  }
};
// Best effort, per warm instance, like the MCP endpoint.
export function createHandler({ store = blobStore, ideas = IDEAS, env = process.env, now = Date.now } = {}) {
  const hits = new Map();
  return async (req, res) => {
    const reply = (status, body, cache = 'no-store') => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': cache });
      res.end(JSON.stringify(body));
    };
    if (!['GET', 'POST'].includes(req.method)) { res.setHeader('Allow', 'GET, POST'); return reply(405, { error: 'method' }); }
    const secret = env.UC_TOKEN_SECRET;
    if (!secret) return reply(500, { error: 'configuration' });
    if (req.method === 'GET') {
      const auth = req.headers?.authorization;
      const token = typeof auth === 'string' && /^Bearer (\S+)$/i.exec(auth)?.[1];
      const identity = verifyToken(token, secret, env.UC_REVOKED);
      if (!identity) return reply(401, { error: 'auth', count: ideas.length });
      if (identity.kind === 'i') {
        try {
          const path = `invites/${identity.subject}.json`, ts = new Date(now()).toISOString();
          const previous = await store.read(path);
          await store.write(path, { firstSeen: previous?.firstSeen || ts, lastSeen: ts });
        } catch { /* Tracking must never block an invitation. */ }
      }
      return reply(200, ideas, 'private, no-store');
    }
    const header = name => { const v = req.headers?.[name]; return (Array.isArray(v) ? v[0] : v) || ''; };
    const ip = header('x-forwarded-for').split(',')[0].trim() || header('x-real-ip').trim() || 'unknown', time = now();
    if (hits.size > 5000) for (const [key, value] of hits) if (value.until <= time) hits.delete(key);
    let hit = hits.get(ip);
    if (!hit || hit.until <= time) hits.set(ip, hit = { count: 0, until: time + 600_000 });
    if (++hit.count > 10) { res.setHeader('Retry-After', String(Math.ceil((hit.until - time) / 1000))); return reply(429, { error: 'rate' }); }
    let body = req.body;
    try { if (typeof body === 'string' || Buffer.isBuffer(body)) body = JSON.parse(String(body)); }
    catch { return reply(400, { error: 'email' }); }
    if (!validEmail(body?.email)) return reply(400, { error: 'email' });
    const email = body.email.toLowerCase();
    try {
      await store.write(`signups/${createHash('sha256').update(email).digest('hex')}.json`, { email, ts: new Date(time).toISOString() });
    } catch { return reply(503, { error: 'store' }); }
    return reply(200, { token: signToken('e', email, secret) });
  };
}
export default createHandler();
