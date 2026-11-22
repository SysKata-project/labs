package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.quarkus.scheduler.Scheduled;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.UUID;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class AssessmentWorker {
  @Inject Store store;
  @Inject javax.sql.DataSource source;
  @Inject com.fasterxml.jackson.databind.ObjectMapper json;
  @Inject Assessment assessments;
  @Inject Evaluator evaluator;
  @Inject ResultDelivery delivery;

  @ConfigProperty(name = "assessment.enabled", defaultValue = "false")
  boolean enabled;

  @Scheduled(every = "2s", concurrentExecution = Scheduled.ConcurrentExecution.SKIP)
  void scheduled() {
    if (enabled) {
      tick();
    }
  }

  public void tick() {
    try (var connection = source.getConnection();
        var lock = connection.prepareStatement("SELECT pg_try_advisory_lock(742194)");
        var row = lock.executeQuery()) {
      if (row.next() && row.getBoolean(1)) {
        try {
          stages();
          jobs();
          delivery.tick();
        } finally {
          try (var unlock = connection.prepareStatement("SELECT pg_advisory_unlock(742194)")) {
            unlock.execute();
          }
        }
      }
    } catch (Exception failure) {
      System.getLogger(AssessmentWorker.class.getName())
          .log(System.Logger.Level.WARNING, "Assessment worker will retry");
    }
  }

  private record Stage(UUID lab, int generation, JsonNode evidence) {}

  private void stages() {
    List<Stage> pending =
        store.transaction(
            connection -> {
              List<Stage> values = new ArrayList<>();
              try (var query =
                      store.prepare(
                          connection,
                          "SELECT lab_id,generation,evidence::text FROM assessment_stages WHERE state='pending' ORDER BY lab_id LIMIT 10");
                  var rows = query.executeQuery()) {
                while (rows.next()) {
                  values.add(
                      new Stage(
                          rows.getObject(1, UUID.class),
                          rows.getInt(2),
                          json.readTree(rows.getString(3))));
                }
              }
              return values;
            });
    for (Stage stage : pending) {
      Lab lab = store.transaction(c -> store.get(c, stage.lab()));
      JsonNode observation =
          valid(lab, stage.generation()) ? evaluator.observe(lab, "stage", stage.evidence()) : null;
      store.transaction(
          connection -> {
            Lab current = store.get(connection, lab.id());
            String state = "superseded";
            JsonNode evidence = stage.evidence();
            if (valid(current, stage.generation()) && observation != null) {
              state = "infrastructure_error";
              if (observation.has("stage")) {
                evidence = observation.path("stage");
                state =
                    evidence.path("phase").asText().equals("fault_observed")
                        ? "complete"
                        : "pending";
                if (evidence.path("phase").asText().equals("waiting")) {
                  evidence = json.createObjectNode();
                }
              }
            }
            store.update(
                connection,
                "UPDATE assessment_stages SET state=?,evidence=?::jsonb WHERE lab_id=? AND generation=?",
                state,
                json.writeValueAsString(evidence),
                lab.id(),
                stage.generation());
            return null;
          });
    }
  }

  private void jobs() {
    List<UUID> ids =
        store.transaction(
            connection -> {
              List<UUID> values = new ArrayList<>();
              try (var query =
                      store.prepare(
                          connection,
                          "SELECT id FROM assessments WHERE state='pending' ORDER BY created_at LIMIT 10");
                  var rows = query.executeQuery()) {
                while (rows.next()) {
                  values.add(rows.getObject(1, UUID.class));
                }
              }
              return values;
            });
    for (UUID id : ids) {
      evaluate(id);
    }
  }

  public void evaluate(UUID id) {
    JsonNode job = store.transaction(c -> assessments.get(c, id));
    if (!job.path("state").asText().equals("pending")) {
      return;
    }
    Assessment.Dispatch dispatch =
        json.convertValue(job.path("request"), Assessment.Dispatch.class);
    Lab lab = store.transaction(c -> store.get(c, dispatch.labId()));
    JsonNode stage =
        store.transaction(
            connection -> {
              try (var query =
                      store.prepare(
                          connection,
                          "SELECT evidence::text FROM assessment_stages WHERE lab_id=? AND generation=? AND state='complete'",
                          lab.id(),
                          dispatch.generation());
                  var rows = query.executeQuery()) {
                return rows.next() ? json.readTree(rows.getString(1)) : json.createObjectNode();
              }
            });
    JsonNode observation =
        valid(lab, dispatch.generation()) ? evaluator.observe(lab, "assess", stage) : null;
    Instant observed = Instant.now();
    store.transaction(
        connection -> {
          Lab current = store.get(connection, lab.id());
          String state = Instant.now().isBefore(current.expiresAt()) ? "superseded" : "expired";
          ObjectNode result = null;
          if (valid(current, dispatch.generation())
              && !store.blocked(connection, current.subject())
              && observation != null) {
            result = result(dispatch, observation, observed, current.scenario());
            state = result.path("outcome").asText();
          }
          store.update(
              connection,
              "UPDATE assessments SET state=?,result=?::jsonb WHERE id=? AND state='pending'",
              state,
              result == null ? null : json.writeValueAsString(result),
              id);
          return null;
        });
  }

  private boolean valid(Lab lab, int generation) {
    return lab.generation() == generation && lab.usable(Instant.now());
  }

  ObjectNode result(
      Assessment.Dispatch d, JsonNode observation, Instant observed, String scenario) {
    ObjectNode value = json.createObjectNode();
    value.put("assessment_id", d.id().toString());
    value.put("lab_session_id", d.labId().toString());
    value.put("session_id", d.sessionId().toString());
    value.put("exercise_instance_id", d.instanceId().toString());
    value.put("attempt_id", d.attemptId().toString());
    value.put("generation", d.generation());
    value.set("versions", json.valueToTree(d.versions()));
    value.put("exercise_revision", d.exerciseRevision());
    value.put("evaluator", d.evaluator());
    value.put("evaluator_version", d.evaluatorVersion());
    value.put("observed_at", observed.toString());
    if (validCriteria(observation.path("criteria"), scenario)) {
      value.put("outcome", "evaluated");
      value.set("criteria", observation.path("criteria"));
    } else {
      value.put("outcome", "infrastructure_error");
      value.put(
          "failure_code",
          observation.path("failure_code").asText().equals("evaluator_timeout")
              ? "evaluator_timeout"
              : "cluster_unavailable");
    }
    return value;
  }

  private boolean validCriteria(JsonNode criteria, String scenario) {
    if (!criteria.isArray() || criteria.size() != Assessment.criteria(scenario).size()) {
      return false;
    }
    var expected =
        new HashSet<>(Assessment.criteria(scenario).stream().map(c -> "criterion." + c).toList());
    for (JsonNode criterion : criteria) {
      if (criterion.size() != 2
          || !criterion.path("passed").isBoolean()
          || !expected.remove(criterion.path("criterion_id").asText())) {
        return false;
      }
    }
    return expected.isEmpty();
  }
}
