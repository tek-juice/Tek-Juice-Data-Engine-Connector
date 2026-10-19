// Tek Juice content API on Express. Replace `items` with your model.
const express = require("express");
const crypto = require("node:crypto");
const fs = require("node:fs");

const TOKEN = process.env.TEKJUICE_CONTENT_API_TOKEN ||
  (process.env.TEKJUICE_CONTENT_API_TOKEN_FILE
    ? fs.readFileSync(process.env.TEKJUICE_CONTENT_API_TOKEN_FILE, "utf8").trim() : "");
const TEMPLATE = process.env.PUBLIC_URL_TEMPLATE || "https://www.example.com/blog/{slug}";
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const items = new Map();

const app = express();
app.use(express.json({ limit: "3mb" }));

// Bearer token shared with the sidecar (constant-time comparison).
app.use("/tekjuice", (req, res, next) => {
  const presented = Buffer.from((req.get("authorization") || "").replace(/^Bearer /, ""));
  const expected = Buffer.from(TOKEN);
  if (!TOKEN || presented.length !== expected.length || !crypto.timingSafeEqual(presented, expected)) {
    return res.status(401).json({ errors: ["unauthorized"] });
  }
  next();
});

const summary = (item) => ({ slug: item.slug, url: TEMPLATE.replace("{slug}", item.slug), id: item.id,
                             idempotency_key: item.idempotency_key });
const validate = (c) => [
  ...(!c.title || c.title.length > 300 ? ["title is required (at most 300 characters)"] : []),
  ...(!c.body ? ["body is required"] : []),
  ...(!SLUG.test(c.slug || "") ? ["slug is invalid"] : []),
];

app.get("/tekjuice/capabilities", (req, res) =>
  res.json({ update: true, validate: true, public_url_template: TEMPLATE }));

app.post("/tekjuice/content/validate", (req, res) => {
  const errors = validate(req.body);
  return errors.length ? res.status(422).json({ errors }) : res.json({ valid: true });
});

app.get("/tekjuice/content/:slug", (req, res) => {
  const item = items.get(req.params.slug);
  return item ? res.json(summary(item)) : res.status(404).json({ errors: ["not found"] });
});

app.post("/tekjuice/content", (req, res) => {
  const errors = validate(req.body);
  if (errors.length) return res.status(422).json({ errors });
  if (items.has(req.body.slug)) return res.status(409).json({ errors: ["slug already exists"] });
  const item = { ...req.body, id: crypto.randomUUID() };
  items.set(item.slug, item);
  return res.status(201).json(summary(item));
});

app.put("/tekjuice/content/:slug", (req, res) => {
  const existing = items.get(req.params.slug);
  if (!existing) return res.status(404).json({ errors: ["not found"] });
  const item = { ...existing, ...req.body, slug: req.params.slug };
  const errors = validate(item);
  if (errors.length) return res.status(422).json({ errors });
  items.set(item.slug, item);
  return res.json(summary(item));
});

app.listen(Number(process.env.PORT || 3000), process.env.HOST || "127.0.0.1",
  () => console.log("content API (Express) listening"));
