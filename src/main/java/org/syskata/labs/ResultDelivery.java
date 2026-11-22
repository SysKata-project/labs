package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class ResultDelivery {
  @Inject Store store;
  @Inject com.fasterxml.jackson.databind.ObjectMapper json;

  @ConfigProperty(name = "assessment.result-url", defaultValue = "https://unconfigured.invalid")
  String base;

  @ConfigProperty(name = "assessment.token-url", defaultValue = "https://unconfigured.invalid")
  String tokenUrl;

  @ConfigProperty(name = "assessment.client-id", defaultValue = "unconfigured")
  String client;

  @ConfigProperty(name = "assessment.client-secret", defaultValue = "unconfigured")
  String secret;

  private record Pending(UUID id, String result, int attempts) {}

  public void tick() {
    List<Pending> pending =
        store.transaction(
            connection -> {
              List<Pending> items = new ArrayList<>();
              try (var query =
                      store.prepare(
                          connection,
                          "SELECT id,result::text,attempts FROM assessments WHERE result IS NOT NULL AND NOT delivered AND next_delivery<=now() ORDER BY created_at LIMIT 10");
                  var rows = query.executeQuery()) {
                while (rows.next()) {
                  items.add(
                      new Pending(
                          rows.getObject(1, UUID.class), rows.getString(2), rows.getInt(3)));
                }
              }
              return items;
            });
    for (Pending job : pending) {
      boolean accepted = send(job.id(), job.result());
      store.transaction(
          connection -> {
            store.update(
                connection,
                "UPDATE assessments SET delivered=?,attempts=attempts+1,next_delivery=? WHERE id=?",
                accepted,
                Instant.now().plusSeconds(Math.min(300, 5L << Math.min(job.attempts(), 6))),
                job.id());
            return null;
          });
    }
  }

  protected record Response(int status, JsonNode body) {}

  protected boolean send(UUID id, String result) {
    try {
      URI issuer = https(tokenUrl);
      URI endpoint = https(base + "/internal/v1/assessments/" + id + "/result");
      String form =
          "grant_type=client_credentials&client_id="
              + encode(client)
              + "&client_secret="
              + encode(secret);
      Response token =
          exchange(
              HttpRequest.newBuilder(issuer)
                  .timeout(Duration.ofSeconds(15))
                  .header("Content-Type", "application/x-www-form-urlencoded")
                  .POST(HttpRequest.BodyPublishers.ofString(form))
                  .build());
      if (token.status() != 200 || !token.body().path("access_token").isTextual()) {
        return false;
      }
      Response response =
          exchange(
              HttpRequest.newBuilder(endpoint)
                  .timeout(Duration.ofSeconds(15))
                  .header("Authorization", "Bearer " + token.body().path("access_token").asText())
                  .header("Content-Type", "application/json")
                  .header("Idempotency-Key", id.toString())
                  .POST(HttpRequest.BodyPublishers.ofString(result))
                  .build());
      JsonNode receipt = response.body();
      return response.status() == 200
          && receipt.path("assessment_id").asText().equals(id.toString())
          && Set.of("applied", "duplicate", "ignored_superseded", "ignored_expired")
              .contains(receipt.path("disposition").asText());
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return false;
    } catch (Exception failure) {
      return false;
    }
  }

  protected Response exchange(HttpRequest request) throws Exception {
    try (HttpClient http =
        HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build()) {
      var response = http.send(request, HttpResponse.BodyHandlers.ofInputStream());
      try (var input = response.body()) {
        var reading =
            java.util.concurrent.CompletableFuture.supplyAsync(
                () -> {
                  try {
                    return input.readNBytes(16385);
                  } catch (java.io.IOException failure) {
                    throw new IllegalStateException("response_read_failed");
                  }
                });
        byte[] bytes = reading.get(15, java.util.concurrent.TimeUnit.SECONDS);
        if (bytes.length > 16384) {
          throw new java.io.IOException("response_too_large");
        }
        return new Response(response.statusCode(), json.readTree(bytes));
      }
    }
  }

  private URI https(String value) {
    URI uri = URI.create(value);
    if (!"https".equals(uri.getScheme())
        || uri.getHost() == null
        || uri.getRawUserInfo() != null
        || uri.getRawQuery() != null
        || uri.getRawFragment() != null) {
      throw new IllegalArgumentException("HTTPS endpoint required");
    }
    return uri;
  }

  private String encode(String value) {
    return URLEncoder.encode(value, StandardCharsets.UTF_8);
  }
}
