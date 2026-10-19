<?php

namespace App\Http\Controllers;

use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Str;

/**
 * Tek Juice content API. The JSON file store keeps the example small;
 * use your Eloquent model (e.g. Post::where('slug', $slug)) instead.
 */
class TekJuiceContentController extends Controller
{
    private function token(): string
    {
        $token = (string) env('TEKJUICE_CONTENT_API_TOKEN', '');
        $file = env('TEKJUICE_CONTENT_API_TOKEN_FILE');
        return $token !== '' ? $token : ($file ? trim((string) file_get_contents($file)) : '');
    }

    private function denied(Request $request): ?JsonResponse
    {
        $token = $this->token();
        $presented = (string) preg_replace('/^Bearer /', '', (string) $request->header('Authorization'));
        return ($token !== '' && hash_equals($token, $presented))
            ? null : response()->json(['errors' => ['unauthorized']], 401);
    }

    private function template(): string
    {
        return (string) env('PUBLIC_URL_TEMPLATE', 'https://www.example.com/blog/{slug}');
    }

    private function path(): string
    {
        return storage_path('app/tekjuice-content.json');
    }

    /** Run $change on the stored items under an exclusive lock. */
    private function withItems(callable $change)
    {
        $handle = fopen($this->path(), 'c+');
        flock($handle, LOCK_EX);
        $raw = stream_get_contents($handle);
        $items = $raw ? json_decode($raw, true) : [];
        [$result, $items] = $change($items);
        ftruncate($handle, 0);
        rewind($handle);
        fwrite($handle, json_encode($items));
        flock($handle, LOCK_UN);
        fclose($handle);
        return $result;
    }

    private function summary(array $item): array
    {
        return ['slug' => $item['slug'], 'url' => str_replace('{slug}', $item['slug'], $this->template()),
                'id' => $item['id'], 'idempotency_key' => $item['idempotency_key'] ?? null];
    }

    private function errors(array $content): array
    {
        $errors = [];
        if (empty($content['title']) || strlen($content['title']) > 300) $errors[] = 'title is required';
        if (empty($content['body'])) $errors[] = 'body is required';
        if (!preg_match('/^[a-z0-9]+(?:-[a-z0-9]+)*$/', $content['slug'] ?? '')) $errors[] = 'slug is invalid';
        return $errors;
    }

    public function capabilities(Request $request): JsonResponse
    {
        return $this->denied($request)
            ?? response()->json(['update' => true, 'validate' => true, 'public_url_template' => $this->template()]);
    }

    public function validateContent(Request $request): JsonResponse
    {
        if ($denied = $this->denied($request)) return $denied;
        $errors = $this->errors($request->json()->all());
        return $errors ? response()->json(['errors' => $errors], 422) : response()->json(['valid' => true]);
    }

    public function show(Request $request, string $slug): JsonResponse
    {
        if ($denied = $this->denied($request)) return $denied;
        $item = $this->withItems(fn ($items) => [$items[$slug] ?? null, $items]);
        return $item ? response()->json($this->summary($item)) : response()->json(['errors' => ['not found']], 404);
    }

    public function store(Request $request): JsonResponse
    {
        if ($denied = $this->denied($request)) return $denied;
        $content = $request->json()->all();
        if ($errors = $this->errors($content)) return response()->json(['errors' => $errors], 422);
        return $this->withItems(function ($items) use ($content) {
            if (isset($items[$content['slug']])) {
                return [response()->json(['errors' => ['slug already exists']], 409), $items];
            }
            $items[$content['slug']] = $content + ['id' => (string) Str::uuid()];
            return [response()->json($this->summary($items[$content['slug']]), 201), $items];
        });
    }

    public function update(Request $request, string $slug): JsonResponse
    {
        if ($denied = $this->denied($request)) return $denied;
        $content = ['slug' => $slug] + $request->json()->all();
        $content['slug'] = $slug;
        return $this->withItems(function ($items) use ($slug, $content) {
            if (!isset($items[$slug])) return [response()->json(['errors' => ['not found']], 404), $items];
            if ($errors = $this->errors($content)) return [response()->json(['errors' => $errors], 422), $items];
            $items[$slug] = array_merge($items[$slug], $content);
            return [response()->json($this->summary($items[$slug])), $items];
        });
    }
}
