<?php
// Tek Juice content API: reference implementation in plain PHP (no dependencies).
// In Laravel / Symfony, map the same routes to a controller over your model.
//
//   TEKJUICE_CONTENT_API_TOKEN=... PUBLIC_URL_TEMPLATE=https://www.example.com/blog/{slug} \
//   php -S 0.0.0.0:3000 content-api.php
//
// Endpoints and responses: see ../node/content-api.js and docs/sidecar.md.

$token = getenv('TEKJUICE_CONTENT_API_TOKEN')
    ?: (getenv('TEKJUICE_CONTENT_API_TOKEN_FILE') ? trim(file_get_contents(getenv('TEKJUICE_CONTENT_API_TOKEN_FILE'))) : '');
$template = getenv('PUBLIC_URL_TEMPLATE') ?: 'https://www.example.com/blog/{slug}';
$dataFile = getenv('CONTENT_FILE') ?: __DIR__ . '/content.json';
$prefix = '/tekjuice';

function send(int $status, array $body): void {
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($body);
    exit;
}

$presented = preg_replace('/^Bearer /', '', $_SERVER['HTTP_AUTHORIZATION'] ?? '');
if ($token === '' || !hash_equals($token, $presented)) {
    send(401, ['errors' => ['unauthorized']]);
}

$items = file_exists($dataFile) ? json_decode(file_get_contents($dataFile), true) : [];
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];
$raw = file_get_contents('php://input');
$body = $raw === '' ? [] : json_decode($raw, true);
if (!is_array($body)) {
    send(400, ['errors' => ['invalid JSON']]);
}
$url = fn(string $slug) => str_replace('{slug}', $slug, $template);
$summary = fn(array $item) => ['slug' => $item['slug'], 'url' => $url($item['slug']), 'id' => $item['id'],
                                'idempotency_key' => $item['idempotency_key'] ?? null];
$validate = function (array $content): array {
    $errors = [];
    if (empty($content['title']) || strlen($content['title']) > 300) $errors[] = 'title is required';
    if (empty($content['body'])) $errors[] = 'body is required';
    if (!preg_match('/^[a-z0-9]+(?:-[a-z0-9]+)*$/', $content['slug'] ?? '')) $errors[] = 'slug is invalid';
    return $errors;
};
$save = fn(array $items) => file_put_contents($dataFile, json_encode($items, JSON_PRETTY_PRINT), LOCK_EX);

if ($method === 'GET' && $path === "$prefix/capabilities") {
    send(200, ['update' => true, 'validate' => true, 'public_url_template' => $template]);
}
if ($method === 'POST' && $path === "$prefix/content/validate") {
    $errors = $validate($body);
    $errors ? send(422, ['errors' => $errors]) : send(200, ['valid' => true]);
}
$slug = preg_match("#^$prefix/content/([a-z0-9-]+)$#", $path, $m) ? $m[1] : null;
if ($method === 'GET' && $slug !== null) {
    isset($items[$slug]) ? send(200, $summary($items[$slug])) : send(404, ['errors' => ['not found']]);
}
if ($method === 'POST' && $path === "$prefix/content") {
    if ($errors = $validate($body)) send(422, ['errors' => $errors]);
    if (isset($items[$body['slug']])) send(409, ['errors' => ['slug already exists']]);
    $items[$body['slug']] = $body + ['id' => bin2hex(random_bytes(16)), 'published_at' => gmdate('c')];
    $save($items);
    send(201, $summary($items[$body['slug']]));
}
if ($method === 'PUT' && $slug !== null) {
    if (!isset($items[$slug])) send(404, ['errors' => ['not found']]);
    if ($errors = $validate(['slug' => $slug] + $body)) send(422, ['errors' => $errors]);
    $items[$slug] = array_merge($items[$slug], $body, ['slug' => $slug, 'updated_at' => gmdate('c')]);
    $save($items);
    send(200, $summary($items[$slug]));
}
send(404, ['errors' => ['no such endpoint']]);
