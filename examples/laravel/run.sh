#!/usr/bin/env sh
# A real Laravel application with two API resources (posts: enabled for Tek
# Juice with x-tekjuice-content-type; products: not enabled), for checking
# content-type discovery and publication against Laravel:
#
#   sh examples/laravel/run.sh                       # serves http://127.0.0.1:18080
#   TEKJUICE_LARAVEL_URL=http://127.0.0.1:18080/api \
#   TEKJUICE_LARAVEL_TOKEN=laravel-token-0123456789abcdef0123456789abcdef \
#     python -m pytest tests/integration/test_content_types_laravel.py
#   docker rm -f tj_laravel_test                     # stop it
#
# Needs Docker (the composer:2 image provides PHP); the app is created in
# $LARAVEL_DIR (default ./.laravel-test, git-ignored) on the first run.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
dir=${LARAVEL_DIR:-$PWD/.laravel-test}
token=${API_TOKEN:-laravel-token-0123456789abcdef0123456789abcdef}
mkdir -p "$dir"
if [ ! -f "$dir/shop/artisan" ]; then
  docker run --rm -u "$(id -u):$(id -g)" -e COMPOSER_HOME=/tmp/composer -v "$dir:/app" -w /app composer:2 \
    composer create-project --no-interaction --prefer-dist laravel/laravel shop
fi
cp -R "$here/files/." "$dir/shop/"
app="$dir/shop/bootstrap/app.php"
grep -q "routes/api.php" "$app" || sed -i "s#web: __DIR__.'/../routes/web.php',#web: __DIR__.'/../routes/web.php',\n        api: __DIR__.'/../routes/api.php',#" "$app"
grep -q "tekjuice.token" "$app" || sed -i "s#    ->withMiddleware(function (Middleware \$middleware): void {#    ->withMiddleware(function (Middleware \$middleware): void {\n        \$middleware->alias(['tekjuice.token' => \\\\App\\\\Http\\\\Middleware\\\\RequireApiToken::class]);#" "$app"
grep -q "^API_TOKEN=" "$dir/shop/.env" || printf '\nAPI_TOKEN=%s\nPUBLIC_SITE_URL=https://shop.example\n' "$token" >> "$dir/shop/.env"
docker rm -f tj_laravel_test >/dev/null 2>&1 || true
docker run -d --name tj_laravel_test -u "$(id -u):$(id -g)" -v "$dir/shop:/app" -w /app -p 127.0.0.1:18080:8000 \
  composer:2 sh -c 'php artisan migrate --force && php artisan serve --host 0.0.0.0 --port 8000' >/dev/null
i=0; until curl -fs http://127.0.0.1:18080/up >/dev/null 2>&1; do i=$((i+1)); [ $i -gt 60 ] && exit 1; sleep 1; done
echo "Laravel serving http://127.0.0.1:18080 (API http://127.0.0.1:18080/api)"
