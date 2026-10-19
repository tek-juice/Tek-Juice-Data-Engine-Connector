FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src src
RUN mvn -q -B package -DskipTests

FROM eclipse-temurin:21-jre-alpine
COPY --from=build /src/target/content-api.jar /app/content-api.jar
USER 10001
CMD ["java", "-jar", "/app/content-api.jar"]
