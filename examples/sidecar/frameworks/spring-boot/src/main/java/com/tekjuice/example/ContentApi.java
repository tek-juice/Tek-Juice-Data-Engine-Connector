package com.tekjuice.example;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/** Tek Juice content API. Replace the map with your repository (JPA, ...). */
@SpringBootApplication
@RestController
@RequestMapping("/tekjuice")
public class ContentApi {
    private static final Pattern SLUG = Pattern.compile("^[a-z0-9]+(?:-[a-z0-9]+)*$");
    private final Map<String, Map<String, Object>> items = new ConcurrentHashMap<>();
    private final String template = Optional.ofNullable(System.getenv("PUBLIC_URL_TEMPLATE"))
            .orElse("https://www.example.com/blog/{slug}");
    private final String token = readToken();

    public static void main(String[] args) {
        SpringApplication.run(ContentApi.class, args);
    }

    private static String readToken() {
        String value = System.getenv("TEKJUICE_CONTENT_API_TOKEN");
        if (value != null && !value.isEmpty()) return value;
        String file = System.getenv("TEKJUICE_CONTENT_API_TOKEN_FILE");
        try {
            return file == null ? "" : Files.readString(Path.of(file)).trim();
        } catch (Exception e) {
            return "";
        }
    }

    private boolean authorized(String header) {
        String presented = header == null ? "" : header.replaceFirst("^Bearer ", "");
        return !token.isEmpty() && MessageDigest.isEqual(presented.getBytes(StandardCharsets.UTF_8),
                token.getBytes(StandardCharsets.UTF_8));
    }

    private static ResponseEntity<Object> errors(int status, String... messages) {
        return ResponseEntity.status(status).body(Map.of("errors", List.of(messages)));
    }

    private Map<String, Object> summary(Map<String, Object> item) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("slug", item.get("slug"));
        out.put("url", template.replace("{slug}", String.valueOf(item.get("slug"))));
        out.put("id", item.get("id"));
        out.put("idempotency_key", item.get("idempotency_key"));
        return out;
    }

    private static List<String> validate(Map<String, Object> c) {
        List<String> errors = new ArrayList<>();
        Object title = c.get("title"), body = c.get("body"), slug = c.get("slug");
        if (!(title instanceof String t) || t.isEmpty() || t.length() > 300) errors.add("title is required");
        if (!(body instanceof String b) || b.isEmpty()) errors.add("body is required");
        if (!(slug instanceof String s) || !SLUG.matcher(s).matches()) errors.add("slug is invalid");
        return errors;
    }

    @GetMapping("/capabilities")
    public ResponseEntity<Object> capabilities(@RequestHeader(value = "Authorization", required = false) String auth) {
        if (!authorized(auth)) return errors(401, "unauthorized");
        return ResponseEntity.ok(Map.of("update", true, "validate", true, "public_url_template", template));
    }

    @PostMapping("/content/validate")
    public ResponseEntity<Object> validateContent(@RequestHeader(value = "Authorization", required = false) String auth,
                                                  @RequestBody Map<String, Object> content) {
        if (!authorized(auth)) return errors(401, "unauthorized");
        List<String> problems = validate(content);
        return problems.isEmpty() ? ResponseEntity.ok(Map.of("valid", true))
                : ResponseEntity.status(422).body(Map.of("errors", problems));
    }

    @GetMapping("/content/{slug}")
    public ResponseEntity<Object> show(@RequestHeader(value = "Authorization", required = false) String auth,
                                       @PathVariable String slug) {
        if (!authorized(auth)) return errors(401, "unauthorized");
        Map<String, Object> item = items.get(slug);
        return item == null ? errors(404, "not found") : ResponseEntity.ok(summary(item));
    }

    @PostMapping("/content")
    public synchronized ResponseEntity<Object> create(@RequestHeader(value = "Authorization", required = false) String auth,
                                                      @RequestBody Map<String, Object> content) {
        if (!authorized(auth)) return errors(401, "unauthorized");
        List<String> problems = validate(content);
        if (!problems.isEmpty()) return ResponseEntity.status(422).body(Map.of("errors", problems));
        String slug = (String) content.get("slug");
        if (items.containsKey(slug)) return errors(409, "slug already exists");
        Map<String, Object> item = new HashMap<>(content);
        item.put("id", UUID.randomUUID().toString());
        items.put(slug, item);
        return ResponseEntity.status(201).body(summary(item));
    }

    @PutMapping("/content/{slug}")
    public synchronized ResponseEntity<Object> update(@RequestHeader(value = "Authorization", required = false) String auth,
                                                      @PathVariable String slug, @RequestBody Map<String, Object> content) {
        if (!authorized(auth)) return errors(401, "unauthorized");
        Map<String, Object> existing = items.get(slug);
        if (existing == null) return errors(404, "not found");
        Map<String, Object> item = new HashMap<>(existing);
        item.putAll(content);
        item.put("slug", slug);
        List<String> problems = validate(item);
        if (!problems.isEmpty()) return ResponseEntity.status(422).body(Map.of("errors", problems));
        items.put(slug, item);
        return ResponseEntity.ok(summary(item));
    }
}
