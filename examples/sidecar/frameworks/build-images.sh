#!/usr/bin/env bash
# Build the framework reference images used by the end-to-end tests.
set -euo pipefail
cd "$(dirname "$0")"
for fw in express laravel rails spring-boot aspnet; do
  echo "== $fw"
  docker build -q -t "tekjuice-example-$fw:test" "$fw"
done
