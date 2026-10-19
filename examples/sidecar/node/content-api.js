// Tek Juice content API: reference implementation in Node.js (no dependencies).
//
// The connector sidecar calls these endpoints after it has authenticated and
// validated an S2S publication. Replace the JSON-file store with your own
// model (Prisma, Sequelize, Mongoose, a CMS API, ...); keep the responses.
//
//   GET  /tekjuice/capabilities      -> {"update": true, "validate": true, "public_url_template": "..."}
//   GET  /tekjuice/content/:slug     -> 200 {"slug","url","id","idempotency_key"} | 404
//   POST /tekjuice/content           -> 201 {"slug","url","id"} | 409 slug taken | 422 {"errors": [...]}
//   PUT  /tekjuice/content/:slug     -> 200 {"slug","url","id"} | 404 | 422
//   POST /tekjuice/content/validate  -> 200 | 422 {"errors": [...]}   (dry run, writes nothing)
//
// Every request must carry "Authorization: Bearer $TEKJUICE_CONTENT_API_TOKEN".
//
//   TEKJUICE_CONTENT_API_TOKEN=... PUBLIC_URL_TEMPLATE=https://www.example.com/blog/{slug} \
//   PORT=3000 node content-api.js

const http = require("node:http");
const fs = require("node:fs");
const crypto = require("node:crypto");

const TOKEN = process.env.TEKJUICE_CONTENT_API_TOKEN ||
  (process.env.TEKJUICE_CONTENT_API_TOKEN_FILE
    ? fs.readFileSync(process.env.TEKJUICE_CONTENT_API_TOKEN_FILE, "utf8").trim() : "");
const TEMPLATE = process.env.PUBLIC_URL_TEMPLATE || "https://www.example.com/blog/{slug}";
const DATA_FILE = process.env.CONTENT_FILE || "./content.json";
const PREFIX = "/tekjuice";

const load = () => (fs.existsSync(DATA_FILE) ? JSON.parse(fs.readFileSync(DATA_FILE, "utf8")) : {});
const save = (items) => fs.writeFileSync(DATA_FILE, JSON.stringify(items, null, 2));
const publicUrl = (slug) => TEMPLATE.replace("{slug}", slug);
const summary = (item) => ({ slug: item.slug, url: publicUrl(item.slug), id: item.id,
                             idempotency_key: item.idempotency_key });

function authorized(req) {
  const presented = Buffer.from((req.headers.authorization || "").replace(/^Bearer /, ""));
  const expected = Buffer.from(TOKEN);
  return TOKEN && presented.length === expected.length && crypto.timingSafeEqual(presented, expected);
}

function validate(content) {
  const errors = [];
  if (!content.title || content.title.length > 300) errors.push("title is required (at most 300 characters)");
  if (!content.body) errors.push("body is required");
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(content.slug || "")) errors.push("slug is invalid");
  return errors;
}

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

const server = http.createServer((req, res) => {
  let raw = "";
  req.on("data", (chunk) => { raw += chunk; });
  req.on("end", () => {
    if (!authorized(req)) return send(res, 401, { errors: ["unauthorized"] });
    const path = req.url.split("?")[0];
    const items = load();
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch { return send(res, 400, { errors: ["invalid JSON"] }); }

    if (req.method === "GET" && path === `${PREFIX}/capabilities`) {
      return send(res, 200, { update: true, validate: true, public_url_template: TEMPLATE });
    }
    if (req.method === "POST" && path === `${PREFIX}/content/validate`) {
      const errors = validate(body);
      return errors.length ? send(res, 422, { errors }) : send(res, 200, { valid: true });
    }
    const match = path.match(new RegExp(`^${PREFIX}/content/([a-z0-9-]+)$`));
    if (req.method === "GET" && match) {
      const item = items[match[1]];
      return item ? send(res, 200, summary(item)) : send(res, 404, { errors: ["not found"] });
    }
    if (req.method === "POST" && path === `${PREFIX}/content`) {
      const errors = validate(body);
      if (errors.length) return send(res, 422, { errors });
      if (items[body.slug]) return send(res, 409, { errors: ["slug already exists"] });
      items[body.slug] = { ...body, id: crypto.randomUUID(), published_at: new Date().toISOString() };
      save(items);
      return send(res, 201, summary(items[body.slug]));
    }
    if (req.method === "PUT" && match) {
      if (!items[match[1]]) return send(res, 404, { errors: ["not found"] });
      const errors = validate({ ...body, slug: match[1] });
      if (errors.length) return send(res, 422, { errors });
      items[match[1]] = { ...items[match[1]], ...body, slug: match[1], updated_at: new Date().toISOString() };
      save(items);
      return send(res, 200, summary(items[match[1]]));
    }
    return send(res, 404, { errors: ["no such endpoint"] });
  });
});

server.listen(Number(process.env.PORT || 3000), process.env.HOST || "127.0.0.1", () => {
  console.log(`content API on ${server.address().address}:${server.address().port}`);
});
