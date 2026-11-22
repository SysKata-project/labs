package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class Evaluator {
  @Inject ObjectMapper json;

  @ConfigProperty(name = "assessment.kubeconfig", defaultValue = "/nonexistent")
  String kubeconfig;

  @ConfigProperty(name = "assessment.helper", defaultValue = "scripts/assess.py")
  String helper;

  @ConfigProperty(name = "labs.route-domain")
  String domain;

  protected Process start() throws java.io.IOException {
    ProcessBuilder builder = new ProcessBuilder("python3", helper);
    // Do not pass result credentials, database credentials or gateway configuration to the adapter.
    String path = builder.environment().get("PATH");
    String ca = builder.environment().get("LAB_ROUTE_CA");
    builder.environment().clear();
    builder.environment().put("PATH", path);
    builder.environment().put("KUBECONFIG", kubeconfig);
    builder.environment().put("LAB_ROUTE_DOMAIN", domain);
    if (ca != null) {
      builder.environment().put("LAB_ROUTE_CA", ca);
    }
    return builder.redirectError(ProcessBuilder.Redirect.DISCARD).start();
  }

  public JsonNode observe(Lab lab, String operation, JsonNode stage) {
    Process process = null;
    try {
      process = start();
      try (var output = process.getOutputStream()) {
        output.write(
            json.writeValueAsBytes(
                Map.of(
                    "lab",
                    lab.id(),
                    "generation",
                    lab.generation(),
                    "scenario",
                    lab.scenario(),
                    "operation",
                    operation,
                    "stage",
                    stage)));
      }
      // The helper has a bounded output contract; kill it if it blocks or exceeds the bound.
      final Process running = process;
      var output =
          java.util.concurrent.CompletableFuture.supplyAsync(
              () -> {
                try (var input = running.getInputStream()) {
                  return input.readNBytes(16385);
                } catch (java.io.IOException failure) {
                  throw new IllegalStateException("adapter_read_failed");
                }
              });
      byte[] bytes = output.get(120, TimeUnit.SECONDS);
      if (bytes.length > 16384
          || !process.waitFor(5, TimeUnit.SECONDS)
          || process.exitValue() != 0) {
        throw new IllegalStateException("adapter_failed");
      }
      return parse(bytes);
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return json.valueToTree(Map.of("failure_code", "evaluator_timeout"));
    } catch (Exception failure) {
      return json.valueToTree(Map.of("failure_code", "cluster_unavailable"));
    } finally {
      if (process != null && process.isAlive()) {
        process.descendants().forEach(ProcessHandle::destroy);
        process.destroyForcibly();
      }
    }
  }

  private JsonNode parse(byte[] bytes) throws java.io.IOException {
    JsonNode value = json.readTree(new String(bytes, StandardCharsets.UTF_8));
    if (value == null || !value.isObject()) {
      throw new IllegalStateException("invalid_adapter_output");
    }
    return value;
  }
}
