<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Http\Resources\PostResource;
use App\Models\Post;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;

class PostController extends Controller
{
    public function index()
    {
        return PostResource::collection(Post::orderBy('id')->get());
    }

    public function store(Request $request)
    {
        $data = $request->validate([
            'title' => 'required|string|max:120', 'slug' => 'required|string|unique:posts,slug',
            'body' => 'required|string', 'excerpt' => 'nullable|string',
            'status' => ['nullable', Rule::in(['draft', 'published'])],
        ]);
        return (new PostResource(Post::create($data)))->response()->setStatusCode(201);
    }

    public function show(Post $post)
    {
        return new PostResource($post);
    }

    public function update(Request $request, Post $post)
    {
        $data = $request->validate([
            'title' => 'sometimes|string|max:120',
            'slug' => ['sometimes', 'string', Rule::unique('posts', 'slug')->ignore($post->id)],
            'body' => 'sometimes|string', 'excerpt' => 'nullable|string',
            'status' => ['nullable', Rule::in(['draft', 'published'])],
        ]);
        $post->update($data);
        return new PostResource($post);
    }
}
