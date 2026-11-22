package org.syskata.labs;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

@QuarkusTest
class AssessmentTest {
  @Inject Assessment assessments;
  @Inject AssessmentWorker worker;
  @Inject Store store;
  @Inject com.fasterxml.jackson.databind.ObjectMapper json;
  @Inject Lifecycle lifecycle;
  @Inject TestEvaluator evaluator;
  @Inject TestDelivery delivery;

  @BeforeEach
  void clear() {
    sql(
        "TRUNCATE assessments,assessment_stages,operations,terminal_tickets,generations,lab_sessions,blocked_subjects");
    evaluator.next = null;
    evaluator.during = () -> {};
    evaluator.calls = 0;
    delivery.accept = false;
    delivery.received.clear();
  }

  void sql(String statement, Object... values) {
    store.transaction(
        c -> {
          store.update(c, statement, values);
          return null;
        });
  }

  Lab ready(String scenario) {
    Lab lab = lifecycle.provision(UUID.randomUUID(), UUID.randomUUID(), "alice", scenario);
    sql("UPDATE lab_sessions SET state='ready' WHERE id=?", lab.id());
    return lifecycle.get(lab.id(), "alice");
  }

  Assessment.Dispatch request(Lab lab) {
    return new Assessment.Dispatch(
        UUID.randomUUID(),
        lab.id(),
        "alice",
        lab.generation(),
        UUID.randomUUID(),
        UUID.randomUUID(),
        UUID.randomUUID(),
        new Assessment.Versions("1.0.0", "0.3.0", "1.0.0"),
        1,
        "openshift." + lab.scenario(),
        1);
  }

  void fails(int code, Runnable call) {
    assertEquals(code, assertThrows(LabFailure.class, call::run).status());
  }

  @Test
  void immutableResultsSurviveRedeliveryAndConflictingRequests() throws Exception {
    Lab lab = ready("foundations");
    var request = request(lab);
    assertEquals(assessments.submit(request), assessments.submit(request));
    fails(409, () -> assessments.submit(request(lab)));
    fails(404, () -> assessments.get(request.id(), "bob"));
    fails(404, () -> assessments.get(UUID.randomUUID(), "alice"));
    worker.tick();
    var result = assessments.get(request.id(), "alice");
    assertEquals("evaluated", result.path("state").asText());
    assertEquals(6, result.path("result").path("criteria").size());
    assertFalse(result.path("delivered").asBoolean());
    assertEquals(1, evaluator.calls);
    sql("UPDATE assessments SET next_delivery=now()");
    delivery.accept = true;
    worker.tick();
    assertEquals(1, evaluator.calls);
    assertEquals(delivery.received.getFirst(), delivery.received.getLast());
    assertTrue(assessments.get(request.id(), "alice").path("delivered").asBoolean());
    assertEquals(assessments.get(request.id(), "alice"), assessments.submit(request));
    var changed = json.valueToTree(request);
    ((com.fasterxml.jackson.databind.node.ObjectNode) changed)
        .put("attemptId", UUID.randomUUID().toString());
    fails(409, () -> assessments.submit(json.convertValue(changed, Assessment.Dispatch.class)));
    worker.evaluate(request.id());
    assertEquals(1, evaluator.calls);
  }

  @Test
  void resetDuringObservationCannotPublishSuccess() {
    Lab lab = ready("administration");
    var request = request(lab);
    assessments.submit(request);
    evaluator.during =
        () -> lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true);
    worker.tick();
    assertEquals("superseded", assessments.get(request.id(), "alice").path("state").asText());
    assertTrue(delivery.received.isEmpty());
    fails(409, () -> assessments.submit(request(lab)));
    evaluator.during = () -> {};
  }

  @Test
  void expiryAndDisabledAccountNeverProduceGrade() {
    Lab lab = ready("administration");
    var request = request(lab);
    assessments.submit(request);
    sql("UPDATE lab_sessions SET expires_at=now()-interval '1 second'");
    worker.tick();
    assertEquals("expired", assessments.get(request.id(), "alice").path("state").asText());
    assertEquals(0, evaluator.calls);
    fails(410, () -> assessments.submit(request(lab)));
    lifecycle.disable("alice");
    fails(403, () -> assessments.get(request.id(), "alice"));
  }

  @Test
  void badAdapterOutputAndInfrastructureFailureRemainUngraded() {
    Lab lab = ready("administration");
    for (Object output :
        new Object[] {
          Map.of("failure_code", "evaluator_timeout"),
          Map.of("criteria", java.util.List.of()),
          Map.of(
              "criteria",
              java.util.Collections.nCopies(
                  4, Map.of("criterion_id", "criterion.context", "passed", true)))
        }) {
      evaluator.next = json.valueToTree(output);
      var request = request(lab);
      assessments.submit(request);
      worker.tick();
      var result = assessments.get(request.id(), "alice").path("result");
      assertEquals("infrastructure_error", result.path("outcome").asText());
      assertFalse(result.has("criteria"));
    }
  }

  @Test
  void faultStagesPersistAndInvalidateOnReset() {
    Lab lab = ready("foundations");
    assertEquals(
        "not_requested", assessments.stage(lab.id(), "alice", 1, false).path("state").asText());
    assessments.stage(lab.id(), "alice", 1, true);
    evaluator.next = json.valueToTree(Map.of("stage", Map.of("phase", "waiting")));
    worker.tick();
    assertEquals("pending", assessments.stage(lab.id(), "alice", 1, false).path("state").asText());
    evaluator.next = null;
    worker.tick();
    assertEquals("complete", assessments.stage(lab.id(), "alice", 1, false).path("state").asText());
    worker.tick();
    assertEquals(2, evaluator.calls);
    sql("UPDATE assessment_stages SET state='pending'");
    evaluator.next = json.valueToTree(Map.of("failure_code", "cluster_unavailable"));
    worker.tick();
    assertEquals(
        "infrastructure_error",
        assessments.stage(lab.id(), "alice", 1, false).path("state").asText());
    sql("UPDATE assessment_stages SET state='pending'");
    lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true);
    worker.tick();
    fails(409, () -> assessments.stage(lab.id(), "alice", 1, true));
  }

  @Test
  void internalApiRequiresAuthenticationAndPinnedVersions() {
    Lab lab = ready("administration");
    var request = request(lab);
    given()
        .contentType("application/json")
        .body(request)
        .post("/internal/assessments")
        .then()
        .statusCode(401);
    given()
        .auth()
        .oauth2(LifecycleTest.TOKEN)
        .contentType("application/json")
        .body(request)
        .post("/internal/assessments")
        .then()
        .statusCode(200);
    given()
        .auth()
        .oauth2(LifecycleTest.TOKEN)
        .queryParam("subject", "alice")
        .get("/internal/assessments/" + request.id())
        .then()
        .statusCode(200);
    fails(400, () -> assessments.submit(null));
    fails(400, () -> assessments.stage(lab.id(), "alice", 1, true));
    var changed = (com.fasterxml.jackson.databind.node.ObjectNode) json.valueToTree(request);
    changed.put("evaluatorVersion", 2);
    fails(400, () -> assessments.submit(json.convertValue(changed, Assessment.Dispatch.class)));
    changed.put("evaluatorVersion", 1);
    changed.put("evaluator", "unsupported");
    changed.put("id", UUID.randomUUID().toString());
    fails(400, () -> assessments.submit(json.convertValue(changed, Assessment.Dispatch.class)));
  }
}
