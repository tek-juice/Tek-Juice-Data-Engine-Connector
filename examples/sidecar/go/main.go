// Tek Juice content API: reference implementation in Go (standard library only).
// In Gin / Echo / Chi, register the same routes over your model.
//
//	TEKJUICE_CONTENT_API_TOKEN=... PUBLIC_URL_TEMPLATE=https://www.example.com/blog/{slug} go run main.go
//
// Endpoints and responses: see ../node/content-api.js and docs/sidecar.md.
package main

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
)

type item struct {
	Slug            string   `json:"slug"`
	Title           string   `json:"title"`
	Body            string   `json:"body"`
	Excerpt         *string  `json:"excerpt"`
	MetaDescription *string  `json:"meta_description"`
	MetaKeywords    []string `json:"meta_keywords"`
	ExecutionID     string   `json:"execution_id"`
	IdempotencyKey  string   `json:"idempotency_key"`
	ID              string   `json:"id"`
}

var (
	token    = readToken()
	template = envOr("PUBLIC_URL_TEMPLATE", "https://www.example.com/blog/{slug}")
	slugRe   = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	pathRe   = regexp.MustCompile(`^/tekjuice/content/([a-z0-9-]+)$`)
	mu       sync.Mutex
	items    = map[string]item{} // replace with your database
)

func readToken() string {
	if v := os.Getenv("TEKJUICE_CONTENT_API_TOKEN"); v != "" {
		return v
	}
	if path := os.Getenv("TEKJUICE_CONTENT_API_TOKEN_FILE"); path != "" {
		if b, err := os.ReadFile(path); err == nil {
			return strings.TrimSpace(string(b))
		}
	}
	return ""
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func send(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func summary(it item) map[string]any {
	return map[string]any{"slug": it.Slug, "url": strings.ReplaceAll(template, "{slug}", it.Slug),
		"id": it.ID, "idempotency_key": it.IdempotencyKey}
}

func validate(it item) []string {
	var errors []string
	if it.Title == "" || len(it.Title) > 300 {
		errors = append(errors, "title is required")
	}
	if it.Body == "" {
		errors = append(errors, "body is required")
	}
	if !slugRe.MatchString(it.Slug) {
		errors = append(errors, "slug is invalid")
	}
	return errors
}

func handler(w http.ResponseWriter, r *http.Request) {
	presented := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if token == "" || subtle.ConstantTimeCompare([]byte(presented), []byte(token)) != 1 {
		send(w, 401, map[string]any{"errors": []string{"unauthorized"}})
		return
	}
	var body item
	if r.Method == http.MethodPost || r.Method == http.MethodPut {
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			send(w, 400, map[string]any{"errors": []string{"invalid JSON"}})
			return
		}
	}
	mu.Lock()
	defer mu.Unlock()
	match := pathRe.FindStringSubmatch(r.URL.Path)
	switch {
	case r.Method == http.MethodGet && r.URL.Path == "/tekjuice/capabilities":
		send(w, 200, map[string]any{"update": true, "validate": true, "public_url_template": template})
	case r.Method == http.MethodPost && r.URL.Path == "/tekjuice/content/validate":
		if errs := validate(body); len(errs) > 0 {
			send(w, 422, map[string]any{"errors": errs})
		} else {
			send(w, 200, map[string]any{"valid": true})
		}
	case r.Method == http.MethodGet && match != nil:
		if it, ok := items[match[1]]; ok {
			send(w, 200, summary(it))
		} else {
			send(w, 404, map[string]any{"errors": []string{"not found"}})
		}
	case r.Method == http.MethodPost && r.URL.Path == "/tekjuice/content":
		if errs := validate(body); len(errs) > 0 {
			send(w, 422, map[string]any{"errors": errs})
		} else if _, exists := items[body.Slug]; exists {
			send(w, 409, map[string]any{"errors": []string{"slug already exists"}})
		} else {
			id := make([]byte, 16)
			_, _ = rand.Read(id)
			body.ID = hex.EncodeToString(id)
			items[body.Slug] = body
			send(w, 201, summary(body))
		}
	case r.Method == http.MethodPut && match != nil:
		existing, ok := items[match[1]]
		body.Slug = match[1]
		if !ok {
			send(w, 404, map[string]any{"errors": []string{"not found"}})
		} else if errs := validate(body); len(errs) > 0 {
			send(w, 422, map[string]any{"errors": errs})
		} else {
			body.ID = existing.ID
			items[body.Slug] = body
			send(w, 200, summary(body))
		}
	default:
		send(w, 404, map[string]any{"errors": []string{"no such endpoint"}})
	}
}

func main() {
	addr := envOr("LISTEN", "127.0.0.1:3000")
	log.Printf("content API on %s", addr)
	log.Fatal(http.ListenAndServe(addr, http.HandlerFunc(handler)))
}
