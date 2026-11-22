package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@ApplicationScoped
public class Assessment {
  @Inject Store store;
  @Inject com.fasterxml.jackson.databind.ObjectMapper json;

  public record Versions(String schema_version, String bundle_version, String policy_version) {}

  public record Dispatch(
      UUID id,
      UUID labId,
      String subject,
      int generation,
      UUID sessionId,
      UUID instanceId,
      UUID attemptId,
      Versions versions,
      int exerciseRevision,
      String evaluator,
      int evaluatorVersion) {}

  public static List<String> criteria(String scenario) {
    return scenario.equals("foundations")
        ? List.of("context", "deployment", "service", "route", "response", "repair")
        : List.of("context", "manifest", "access", "quota");
  }

  public JsonNode submit(Dispatch request) {
    validate(request);
    return store.transaction(
        connection -> {
          store.lock(connection);
          Lab lab = store.get(connection, request.labId());
          owned(connection, lab, request.subject());
          try (var query =
                  store.prepare(
                      connection,
                      "SELECT request::text FROM assessments WHERE id=?",
                      request.id());
              var rows = query.executeQuery()) {
            if (rows.next()) {
              if (!json.readTree(rows.getString(1)).equals(json.valueToTree(request))) {
                throw new LabFailure(409, "assessment_conflict");
              }
              return get(connection, request.id());
            }
          }
          ready(lab, request.generation());
          if (!request.evaluator().equals("openshift." + lab.scenario())) {
            throw new LabFailure(400, "unsupported_evaluator");
          }
          try (var query =
                  store.prepare(
                      connection,
                      "SELECT id FROM assessments WHERE lab_id=? AND generation=? AND state='pending'",
                      lab.id(),
                      lab.generation());
              var rows = query.executeQuery()) {
            if (rows.next()) {
              throw new LabFailure(409, "assessment_pending");
            }
          }
          store.update(
              connection,
              "INSERT INTO assessments(id,lab_id,generation,request) VALUES (?,?,?,?::jsonb)",
              request.id(),
              lab.id(),
              lab.generation(),
              json.writeValueAsString(request));
          return get(connection, request.id());
        });
  }

  public JsonNode get(UUID id, String subject) {
    return store.transaction(
        connection -> {
          JsonNode job = get(connection, id);
          Lab lab =
              store.get(connection, UUID.fromString(job.path("request").path("labId").asText()));
          owned(connection, lab, subject);
          return job;
        });
  }

  JsonNode get(java.sql.Connection connection, UUID id) throws Exception {
    try (var query =
            store.prepare(
                connection,
                "SELECT jsonb_build_object('request',request,'state',state,'result',result,'delivered',delivered)::text FROM assessments WHERE id=?",
                id);
        var rows = query.executeQuery()) {
      if (!rows.next()) {
        throw new LabFailure(404, "assessment_not_found");
      }
      return json.readTree(rows.getString(1));
    }
  }

  public JsonNode stage(UUID id, String subject, int generation, boolean create) {
    return store.transaction(
        connection -> {
          Lab lab = store.get(connection, id);
          owned(connection, lab, subject);
          ready(lab, generation);
          if (!lab.scenario().equals("foundations")) {
            throw new LabFailure(400, "invalid_stage");
          }
          if (create) {
            store.update(
                connection,
                "INSERT INTO assessment_stages(lab_id,generation) VALUES (?,?) ON CONFLICT DO NOTHING",
                id,
                generation);
          }
          try (var query =
                  store.prepare(
                      connection,
                      "SELECT state,evidence::text FROM assessment_stages WHERE lab_id=? AND generation=?",
                      id,
                      generation);
              var rows = query.executeQuery()) {
            return rows.next()
                ? json.valueToTree(
                    Map.of(
                        "state",
                        rows.getString(1),
                        "phase",
                        json.readTree(rows.getString(2)).path("phase").asText("pending")))
                : json.valueToTree(Map.of("state", "not_requested"));
          }
        });
  }

  void owned(java.sql.Connection connection, Lab lab, String subject) throws Exception {
    if (!lab.subject().equals(subject)) {
      throw new LabFailure(404, "lab_not_found");
    }
    if (store.blocked(connection, subject)) {
      throw new LabFailure(403, "account_disabled");
    }
  }

  void ready(Lab lab, int generation) {
    if (lab.generation() != generation) {
      throw new LabFailure(409, "stale_generation");
    }
    if (!lab.usable(Instant.now())) {
      throw new LabFailure(410, "lab_unavailable");
    }
  }

  private void validate(Dispatch r) {
    if (r == null) {
      throw new LabFailure(400, "invalid_request");
    }
    if (java.util.stream.Stream.of(
            r.id(),
            r.labId(),
            r.sessionId(),
            r.instanceId(),
            r.attemptId(),
            r.subject(),
            r.versions(),
            r.evaluator())
        .anyMatch(java.util.Objects::isNull)) {
      throw new LabFailure(400, "invalid_request");
    }
    validateVersions(r);
  }

  private void validateVersions(Dispatch r) {
    Versions v = r.versions();
    if (!"1.0.0".equals(v.schema_version())
        || !"1.0.0".equals(v.policy_version())
        || v.bundle_version() == null
        || !v.bundle_version().matches("[0-9]+\\.[0-9]+\\.[0-9]+")
        || r.evaluatorVersion() != 1
        || r.exerciseRevision() != 1) {
      throw new LabFailure(400, "unsupported_version");
    }
  }
}
