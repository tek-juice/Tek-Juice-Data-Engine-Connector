// Two small REST backends with different conventions, for real-HTTP tests of
// content-type discovery and publication (no dependencies; node >= 18).
//
//   STYLE=laravel   /api/posts, /api/products (numeric ids, PUT, {"data": ...},
//                   422 {"message", "errors": {field: [..]}}), OpenAPI at
//                   /api/openapi.json with x-tekjuice-content-type on posts
//   STYLE=manifest  /v1/events (slug identity, PATCH), /v1/issues (no public
//                   page; a send action), manifest at /v1/capabilities
// TOKEN is required as "Authorization: Bearer <TOKEN>". PORT 0 = any port;
// the chosen port is printed as "listening <port>".
const http = require('http');

const STYLE = process.env.STYLE || 'laravel';
const TOKEN = process.env.TOKEN || '';
const db = {};
let nextId = 1;

const html = { type: 'string', format: 'html' };
const openapi = {
  openapi: '3.0.3', info: { title: 'Laravel-style shop', version: '1.0' },
  components: { schemas: {
    Post: { type: 'object', required: ['title', 'body', 'slug'], properties: {
      title: { type: 'string', maxLength: 120 }, body: html, slug: { type: 'string' },
      excerpt: { type: 'string' }, status: { type: 'string', enum: ['draft', 'published'] } } },
    Product: { type: 'object', required: ['name', 'slug', 'price'], properties: {
      name: { type: 'string' }, slug: { type: 'string' }, description: html,
      price: { type: 'number', minimum: 0 } } } } },
  paths: {
    '/posts': { post: { 'x-tekjuice-content-type': { name: 'post',
      public_url_template: 'https://laravel.customer.example/blog/{slug}' },
      requestBody: { content: { 'application/json': { schema: { $ref: '#/components/schemas/Post' } } } } } },
    '/posts/{post}': { put: {}, get: {} },
    '/products': { post: { requestBody: { content: { 'application/json': {
      schema: { $ref: '#/components/schemas/Product' } } } } } },
    '/products/{product}': { put: {} },
  },
};
const manifest = {
  update: true, public_url_template: 'https://manifest.customer.example/blog/{slug}',
  content_types: [
    { name: 'event', label: 'Events', identity: 'slug',
      public_url_template: 'https://manifest.customer.example/events/{slug}',
      schema: { type: 'object', required: ['name', 'slug', 'starts_at'], properties: {
        name: { type: 'string' }, slug: { type: 'string' }, description: html,
        starts_at: { type: 'string', format: 'date-time' }, capacity: { type: 'integer', minimum: 1 } } },
      operations: { create: { method: 'POST', path: '/events' }, update: { method: 'PATCH', path: '/events/{slug}' } } },
    { name: 'newsletter_issue', label: 'Newsletter issues', public: false,
      schema: { type: 'object', required: ['subject', 'html', 'slug'], properties: {
        subject: { type: 'string' }, html: html, slug: { type: 'string' } } },
      operations: { create: { method: 'POST', path: '/issues' }, send: { method: 'POST', path: '/issues/{slug}/send' } } },
  ],
};
const required = { posts: ['title', 'body', 'slug'], products: ['name', 'slug', 'price'],
                   events: ['name', 'slug', 'starts_at'], issues: ['subject', 'html', 'slug'] };

function reply(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(body === undefined ? '' : JSON.stringify(body));
}

const prefix = STYLE === 'laravel' ? '/api' : '/v1';
http.createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    if (req.headers.authorization !== `Bearer ${TOKEN}`) return reply(res, 401, { message: 'Unauthenticated.' });
    if (!req.url.startsWith(prefix)) return reply(res, 404, { message: 'Not Found' });
    const path = req.url.slice(prefix.length);
    if (STYLE === 'laravel' && path === '/openapi.json') return reply(res, 200, openapi);
    if (STYLE === 'manifest' && path === '/capabilities') return reply(res, 200, manifest);
    if (path === '/_records') return reply(res, 200, db);
    const [collection, key, action] = path.split('/').filter(Boolean);
    if (!required[collection]) return reply(res, 404, { message: 'Not Found' });
    const records = (db[collection] = db[collection] || []);
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch (e) { return reply(res, 400, { message: 'bad json' }); }
    const find = (k) => records.find((r) => String(r.id) === k || r.slug === k);
    const wrap = (r) => (STYLE === 'laravel' ? { data: r } : r);
    if (action === 'send') return reply(res, 202, { sent: true });
    if (req.method === 'POST' && !key) {
      const missing = required[collection].filter((f) => body[f] === undefined);
      if (missing.length) {
        return reply(res, 422, { message: 'The given data was invalid.',
          errors: Object.fromEntries(missing.map((f) => [f, [`The ${f} field is required.`]])) });
      }
      const replay = records.find((r) => r.idempotency_key && r.idempotency_key === req.headers['idempotency-key']);
      if (replay) return reply(res, 200, wrap(replay));
      if (records.some((r) => r.slug === body.slug)) return reply(res, 409, { message: 'The slug has already been taken.' });
      const record = { id: nextId++, ...body, idempotency_key: req.headers['idempotency-key'] };
      if (STYLE === 'laravel') record.url = `https://laravel.customer.example/${collection}/${body.slug}`;
      records.push(record);
      return reply(res, 201, wrap(record));
    }
    if ((req.method === 'PUT' || req.method === 'PATCH') && key) {
      const record = find(key);
      if (!record) return reply(res, 404, { message: 'Not Found' });
      Object.assign(record, body);
      return reply(res, 200, wrap(record));
    }
    if (req.method === 'GET' && key) {
      const record = find(key);
      return record ? reply(res, 200, wrap(record)) : reply(res, 404, { message: 'Not Found' });
    }
    return reply(res, 405, { message: 'Method Not Allowed' });
  });
}).listen(Number(process.env.PORT || 0), '127.0.0.1', function () {
  console.log(`listening ${this.address().port}`);
});
