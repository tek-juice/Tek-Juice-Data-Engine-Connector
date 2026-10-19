Rails.application.routes.draw do
  # Tek Juice content API
  scope "/tekjuice" do
    get "capabilities", to: "tekjuice_content#capabilities"
    post "content/validate", to: "tekjuice_content#validate_content"
    get "content/:slug", to: "tekjuice_content#show"
    post "content", to: "tekjuice_content#create"
    put "content/:slug", to: "tekjuice_content#update"
  end
end
