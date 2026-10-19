FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY aspnet.csproj .
RUN dotnet restore
COPY Program.cs .
RUN dotnet publish -c Release -o /out --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /out .
USER app
CMD ["sh", "-c", "ASPNETCORE_URLS=http://${HOST:-127.0.0.1}:${PORT:-3000} exec dotnet content-api.dll"]
