# Framework reference content APIs

The same content API (`../../../docs/content-api.openapi.yaml`) in common
frameworks. Each directory is a minimal, runnable app with a Dockerfile; the
store is in memory (or a file for Laravel) so the examples stay small. In
your application, replace it with your model and keep the routes and
responses.

| Framework | Directory | Build |
|---|---|---|
| Express (Node.js) | `express/` | `docker build -t tekjuice-example-express express` |
| Laravel (PHP) | `laravel/` | `docker build -t tekjuice-example-laravel laravel` |
| Ruby on Rails | `rails/` | `docker build -t tekjuice-example-rails rails` |
| Spring Boot (Java) | `spring-boot/` | `docker build -t tekjuice-example-spring-boot spring-boot` |
| ASP.NET Core (C#) | `aspnet/` | `docker build -t tekjuice-example-aspnet aspnet` |

`./build-images.sh` builds all five. Every image reads `PORT`,
`TEKJUICE_CONTENT_API_TOKEN` (or `TEKJUICE_CONTENT_API_TOKEN_FILE`) and
`PUBLIC_URL_TEMPLATE`, and serves the API under `/tekjuice`.
`tests/integration/test_sidecar_languages.py` runs the sidecar end to end
against each built image.
