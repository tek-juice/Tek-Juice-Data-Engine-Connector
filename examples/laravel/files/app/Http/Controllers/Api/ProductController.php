<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\Product;
use Illuminate\Http\Request;

class ProductController extends Controller
{
    public function index()
    {
        return ['data' => Product::orderBy('id')->get()];
    }

    public function store(Request $request)
    {
        $data = $request->validate([
            'name' => 'required|string', 'slug' => 'required|string|unique:products,slug',
            'description' => 'nullable|string', 'price' => 'required|numeric|min:0',
            'sku' => 'required|string|unique:products,sku',
        ]);
        return response()->json(['data' => Product::create($data)], 201);
    }

    public function update(Request $request, Product $product)
    {
        $product->update($request->validate(['name' => 'sometimes|string', 'price' => 'sometimes|numeric|min:0']));
        return ['data' => $product];
    }
}
