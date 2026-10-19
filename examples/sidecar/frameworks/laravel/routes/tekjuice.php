<?php
// Tek Juice content API routes (stateless: no session or CSRF middleware).
use App\Http\Controllers\TekJuiceContentController;
use Illuminate\Support\Facades\Route;

Route::get('capabilities', [TekJuiceContentController::class, 'capabilities']);
Route::post('content/validate', [TekJuiceContentController::class, 'validateContent']);
Route::get('content/{slug}', [TekJuiceContentController::class, 'show']);
Route::post('content', [TekJuiceContentController::class, 'store']);
Route::put('content/{slug}', [TekJuiceContentController::class, 'update']);
