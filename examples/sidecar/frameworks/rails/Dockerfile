# A fresh Rails 7.2 API app with the Tek Juice content API added.
FROM ruby:3.3-slim
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libyaml-dev \
 && rm -rf /var/lib/apt/lists/*
RUN gem install rails -v 7.2.2 --no-document
WORKDIR /app
RUN rails new . --api --minimal --skip-git --skip-docker --skip-test --skip-active-record --skip-bundle --force \
 && bundle install
COPY config/routes.rb config/routes.rb
COPY app/controllers/tekjuice_content_controller.rb app/controllers/tekjuice_content_controller.rb
# Development mode keeps the example free of TLS/secret-key setup; host
# authorisation there allows localhost and 127.0.0.1.
ENV RAILS_ENV=development
CMD ["sh", "-c", "bin/rails server -b ${HOST:-127.0.0.1} -p ${PORT:-3000}"]
