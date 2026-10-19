# A fresh Laravel 11 app with the Tek Juice content API added.
FROM composer:2.8
WORKDIR /app
RUN composer create-project --no-interaction --prefer-dist --no-dev laravel/laravel . "11.*" \
 && rm -rf tests
COPY bootstrap/app.php bootstrap/app.php
COPY routes/tekjuice.php routes/tekjuice.php
COPY app/Http/Controllers/TekJuiceContentController.php app/Http/Controllers/TekJuiceContentController.php
ENV APP_ENV=production APP_DEBUG=false LOG_CHANNEL=stderr SESSION_DRIVER=array CACHE_STORE=array
# PHP's server with Laravel's router script: unlike `artisan serve`, it passes
# the container environment (TEKJUICE_*, PUBLIC_URL_TEMPLATE) through, as
# php-fpm does in production.
WORKDIR /app/public
CMD ["sh", "-c", "exec php -S ${HOST:-127.0.0.1}:${PORT:-3000} ../vendor/laravel/framework/src/Illuminate/Foundation/resources/server.php"]
