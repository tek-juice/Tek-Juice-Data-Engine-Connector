<?php

use App\Http\Controllers\Api\PostController;
use App\Http\Controllers\Api\ProductController;
use Illuminate\Support\Facades\Route;

Route::middleware('tekjuice.token')->group(function () {
    Route::get('/openapi.json', fn () => response()->file(base_path('openapi.json')));
    Route::apiResource('posts', PostController::class)->except(['destroy']);
    Route::apiResource('products', ProductController::class)->only(['index', 'store', 'update']);
});
