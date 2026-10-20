<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;

class RequireApiToken
{
    public function handle(Request $request, Closure $next)
    {
        $token = env('API_TOKEN');
        if (! $token || ! hash_equals('Bearer '.$token, (string) $request->header('Authorization'))) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }
        return $next($request);
    }
}
