# tekjuice-connector sidecar: S2S publication for a backend in any language.
#
#   docker build -t tekjuice-connector .        (published: ghcr.io/tek-juice/tekjuice-connector)
#   docker run -d --name tekjuice-connector \
#     -v tekjuice-data:/var/lib/tekjuice \
#     -e TEKJUICE_WEBSITE_ID=... -e TEKJUICE_CONNECTOR_KEY_FILE=/run/secrets/tekjuice_connector_key \
#     -e TEKJUICE_DATA_ENGINE_URL=https://... -e TEKJUICE_API_BASE_URL=https://api.example.com \
#     -e TEKJUICE_CONTENT_API_URL=http://backend:3000/tekjuice \
#     -e TEKJUICE_CONTENT_API_TOKEN_FILE=/run/secrets/tekjuice_content_api_token \
#     ghcr.io/tek-juice/tekjuice-connector:0.7.0
#
# The receiver secret is generated on first start and kept in the
# /var/lib/tekjuice volume; nothing secret is baked into the image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-compile . && rm -rf /src \
 && useradd --system --uid 10001 --home-dir /var/lib/tekjuice tekjuice \
 && mkdir -p /var/lib/tekjuice && chown tekjuice /var/lib/tekjuice

USER tekjuice
WORKDIR /var/lib/tekjuice
VOLUME ["/var/lib/tekjuice"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["tekjuice"]
CMD ["serve"]
