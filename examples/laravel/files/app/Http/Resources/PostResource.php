<?php

namespace App\Http\Resources;

use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

class PostResource extends JsonResource
{
    public function toArray(Request $request): array
    {
        return [
            'id' => $this->id, 'title' => $this->title, 'slug' => $this->slug, 'body' => $this->body,
            'excerpt' => $this->excerpt, 'status' => $this->status,
            'url' => rtrim(env('PUBLIC_SITE_URL', 'https://shop.example'), '/').'/blog/'.$this->slug,
        ];
    }
}
