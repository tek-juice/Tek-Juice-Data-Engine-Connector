// Tek Juice content API on ASP.NET Core minimal APIs.
// Replace the dictionary with your EF Core DbContext.
using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

var template = Environment.GetEnvironmentVariable("PUBLIC_URL_TEMPLATE") ?? "https://www.example.com/blog/{slug}";
var tokenFile = Environment.GetEnvironmentVariable("TEKJUICE_CONTENT_API_TOKEN_FILE");
var token = Environment.GetEnvironmentVariable("TEKJUICE_CONTENT_API_TOKEN") is { Length: > 0 } t ? t
    : tokenFile is not null ? File.ReadAllText(tokenFile).Trim() : "";
var slugPattern = new Regex("^[a-z0-9]+(?:-[a-z0-9]+)*$");
var items = new ConcurrentDictionary<string, JsonObject>();
var writeLock = new object();

app.Use(async (context, next) =>
{
    if (context.Request.Path.StartsWithSegments("/tekjuice"))
    {
        var presented = context.Request.Headers.Authorization.ToString();
        presented = presented.StartsWith("Bearer ") ? presented[7..] : presented;
        if (token.Length == 0 || !CryptographicOperations.FixedTimeEquals(
                Encoding.UTF8.GetBytes(presented), Encoding.UTF8.GetBytes(token)))
        {
            context.Response.StatusCode = 401;
            await context.Response.WriteAsJsonAsync(new { errors = new[] { "unauthorized" } });
            return;
        }
    }
    await next();
});

object Summary(JsonObject item) => new
{
    slug = (string?)item["slug"],
    url = template.Replace("{slug}", (string?)item["slug"]),
    id = (string?)item["id"],
    idempotency_key = (string?)item["idempotency_key"],
};

List<string> Validate(JsonObject c)
{
    var errors = new List<string>();
    var title = (string?)c["title"];
    if (string.IsNullOrEmpty(title) || title.Length > 300) errors.Add("title is required");
    if (string.IsNullOrEmpty((string?)c["body"])) errors.Add("body is required");
    if (!slugPattern.IsMatch((string?)c["slug"] ?? "")) errors.Add("slug is invalid");
    return errors;
}

app.MapGet("/tekjuice/capabilities", () => Results.Json(new { update = true, validate = true, public_url_template = template }));

app.MapPost("/tekjuice/content/validate", (JsonObject content) =>
{
    var errors = Validate(content);
    return errors.Count > 0 ? Results.Json(new { errors }, statusCode: 422) : Results.Json(new { valid = true });
});

app.MapGet("/tekjuice/content/{slug}", (string slug) =>
    items.TryGetValue(slug, out var item) ? Results.Json(Summary(item))
        : Results.Json(new { errors = new[] { "not found" } }, statusCode: 404));

app.MapPost("/tekjuice/content", (JsonObject content) =>
{
    var errors = Validate(content);
    if (errors.Count > 0) return Results.Json(new { errors }, statusCode: 422);
    lock (writeLock)
    {
        var slug = (string)content["slug"]!;
        if (items.ContainsKey(slug)) return Results.Json(new { errors = new[] { "slug already exists" } }, statusCode: 409);
        content["id"] = Guid.NewGuid().ToString();
        items[slug] = content;
        return Results.Json(Summary(content), statusCode: 201);
    }
});

app.MapPut("/tekjuice/content/{slug}", (string slug, JsonObject content) =>
{
    lock (writeLock)
    {
        if (!items.TryGetValue(slug, out var existing))
            return Results.Json(new { errors = new[] { "not found" } }, statusCode: 404);
        var merged = (JsonObject)existing.DeepClone();
        foreach (var (key, value) in content) merged[key] = value?.DeepClone();
        merged["slug"] = slug;
        var errors = Validate(merged);
        if (errors.Count > 0) return Results.Json(new { errors }, statusCode: 422);
        items[slug] = merged;
        return Results.Json(Summary(merged));
    }
});

app.Run();
