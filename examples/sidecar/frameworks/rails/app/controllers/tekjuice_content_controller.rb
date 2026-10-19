# Tek Juice content API. The in-memory store keeps the example small;
# use your model (e.g. Post.find_by(slug: params[:slug])) instead.
class TekjuiceContentController < ActionController::API
  ITEMS = {}
  LOCK = Mutex.new
  SLUG = /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/

  before_action :authenticate

  def capabilities
    render json: { update: true, validate: true, public_url_template: template }
  end

  def validate_content
    errors = content_errors(body)
    errors.empty? ? render(json: { valid: true }) : render(json: { errors: errors }, status: 422)
  end

  def show
    item = LOCK.synchronize { ITEMS[params[:slug]] }
    item ? render(json: summary(item)) : render(json: { errors: ["not found"] }, status: 404)
  end

  def create
    content = body
    errors = content_errors(content)
    return render(json: { errors: errors }, status: 422) unless errors.empty?
    LOCK.synchronize do
      return render(json: { errors: ["slug already exists"] }, status: 409) if ITEMS.key?(content["slug"])
      ITEMS[content["slug"]] = content.merge("id" => SecureRandom.uuid)
      render json: summary(ITEMS[content["slug"]]), status: 201
    end
  end

  def update
    LOCK.synchronize do
      existing = ITEMS[params[:slug]]
      return render(json: { errors: ["not found"] }, status: 404) unless existing
      content = existing.merge(body).merge("slug" => params[:slug])
      errors = content_errors(content)
      return render(json: { errors: errors }, status: 422) unless errors.empty?
      ITEMS[params[:slug]] = content
      render json: summary(content)
    end
  end

  private

  def body
    JSON.parse(request.raw_post.presence || "{}")
  end

  def template
    ENV.fetch("PUBLIC_URL_TEMPLATE", "https://www.example.com/blog/{slug}")
  end

  def token
    ENV["TEKJUICE_CONTENT_API_TOKEN"].presence ||
      (ENV["TEKJUICE_CONTENT_API_TOKEN_FILE"].presence && File.read(ENV["TEKJUICE_CONTENT_API_TOKEN_FILE"]).strip) || ""
  end

  def authenticate
    presented = request.authorization.to_s.delete_prefix("Bearer ")
    expected = token
    unless expected.present? && ActiveSupport::SecurityUtils.secure_compare(presented, expected)
      render json: { errors: ["unauthorized"] }, status: 401
    end
  end

  def summary(item)
    { slug: item["slug"], url: template.sub("{slug}", item["slug"]), id: item["id"],
      idempotency_key: item["idempotency_key"] }
  end

  def content_errors(content)
    errors = []
    errors << "title is required" if content["title"].blank? || content["title"].length > 300
    errors << "body is required" if content["body"].blank?
    errors << "slug is invalid" unless content["slug"].to_s.match?(SLUG)
    errors
  end
end
