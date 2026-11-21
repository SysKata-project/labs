package org.syskata.labs;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

@QuarkusTest
class LifecycleTest {
  static final String TOKEN = "synthetic-lab-test-service-credential-only";
  @Inject Lifecycle lifecycle;
  @Inject Store store;
  @Inject Reconciler worker;
  @Inject TestCluster cluster;

  @BeforeEach
  void clear() {
    cluster.failure(false);
    store.transaction(
        connection -> {
          store.update(
              connection,
              "TRUNCATE operations,terminal_tickets,generations,lab_sessions,blocked_subjects");
          return null;
        });
  }

  Lab provision(String subject) {
    return lifecycle.provision(UUID.randomUUID(), UUID.randomUUID(), subject, "foundations");
  }

  Lab ready(String subject) {
    Lab lab = provision(subject);
    sql("UPDATE lab_sessions SET state='ready' WHERE id=?", lab.id());
    return lifecycle.get(lab.id(), subject);
  }

  void sql(String query, Object... args) {
    store.transaction(
        connection -> {
          store.update(connection, query, args);
          return null;
        });
  }

  void fails(int status, Runnable call) {
    assertEquals(status, assertThrows(LabFailure.class, call::run).status());
  }

  @Test
  void provisioningIsIdempotentAndCapacityIsAtomic() throws Exception {
    UUID key = UUID.randomUUID();
    UUID id = UUID.randomUUID();
    Lab first = lifecycle.provision(key, id, "alice", "foundations");
    assertEquals(first, lifecycle.provision(key, id, "alice", "foundations"));
    fails(409, () -> lifecycle.provision(UUID.randomUUID(), id, "alice", "foundations"));
    fails(409, () -> lifecycle.provision(key, id, "bob", "foundations"));
    try (var executor = Executors.newFixedThreadPool(4)) {
      var requests = new ArrayList<Callable<Boolean>>();
      for (int i = 0; i < 4; i++) {
        final int index = i;
        requests.add(
            () -> {
              try {
                provision("subject-" + index);
                return true;
              } catch (LabFailure full) {
                assertEquals(429, full.status());
                return false;
              }
            });
      }
      long admitted = 0;
      for (var result : executor.invokeAll(requests)) {
        if (result.get()) {
          admitted++;
        }
      }
      assertEquals(1, admitted);
    }
    fails(400, () -> lifecycle.provision(key, id, "", "foundations"));
    fails(400, () -> lifecycle.provision(key, id, "a", "unknown"));
    fails(404, () -> lifecycle.get(id, "bob"));
    fails(404, () -> lifecycle.get(UUID.randomUUID(), "alice"));
  }

  @Test
  void resetKeepsDeadlineAndRevokesOldGeneration() {
    Lab lab = ready("alice");
    UUID key = UUID.randomUUID();
    Lab reset = lifecycle.change(key, lab.id(), "alice", lab.revision(), 1, true);
    assertEquals(2, reset.generation());
    assertEquals(lab.expiresAt(), reset.expiresAt());
    assertEquals(reset, lifecycle.change(key, lab.id(), "alice", lab.revision(), 1, true));
    fails(
        409, () -> lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true));
    fails(
        409,
        () -> lifecycle.change(UUID.randomUUID(), lab.id(), "alice", reset.revision(), 2, true));
    fails(410, () -> lifecycle.authorize(lab.id(), "alice", 1, false));
    sql("UPDATE lab_sessions SET state='ready' WHERE id=?", lab.id());
    fails(409, () -> lifecycle.authorize(lab.id(), "alice", 1, false));
    assertEquals(2, lifecycle.authorize(lab.id(), "alice", 2, true).generation());
    Lab closed = lifecycle.change(UUID.randomUUID(), lab.id(), "alice", reset.revision(), 2, false);
    assertEquals("closing", closed.state());
    assertFalse(closed.cleanupComplete());
  }

  @Test
  void ticketsAreBoundExpiringAndSingleUse() {
    Lab lab = ready("alice");
    String ticket = UUID.randomUUID().toString();
    Instant expiry = Instant.now().plusSeconds(45).truncatedTo(ChronoUnit.MICROS);
    lifecycle.registerTicket(lab.id(), "alice", 1, Lifecycle.hash(ticket), expiry);
    lifecycle.registerTicket(lab.id(), "alice", 1, Lifecycle.hash(ticket), expiry);
    fails(
        409,
        () ->
            lifecycle.registerTicket(
                lab.id(), "alice", 1, Lifecycle.hash(ticket), expiry.plusSeconds(1)));
    fails(404, () -> lifecycle.redeem(lab.id(), "bob", 1, ticket));
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, "short"));
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, UUID.randomUUID().toString()));
    assertEquals(lab.id(), lifecycle.redeem(lab.id(), "alice", 1, ticket).id());
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, ticket));
    fails(400, () -> lifecycle.registerTicket(lab.id(), "alice", 1, "invalid", expiry));
    fails(
        400,
        () ->
            lifecycle.registerTicket(
                lab.id(), "alice", 1, Lifecycle.hash(ticket), Instant.now().minusSeconds(1)));
    fails(
        400,
        () ->
            lifecycle.registerTicket(
                lab.id(), "alice", 1, Lifecycle.hash(ticket), Instant.now().plusSeconds(61)));
    sql("UPDATE lab_sessions SET expires_at=now()+interval '10 seconds' WHERE id=?", lab.id());
    fails(
        400, () -> lifecycle.registerTicket(lab.id(), "alice", 1, Lifecycle.hash(ticket), expiry));
  }

  @Test
  void expiryIdleAndAccountDisableFailClosed() {
    Lab lab = ready("alice");
    sql("UPDATE lab_sessions SET last_activity=now()-interval '601 seconds' WHERE id=?", lab.id());
    fails(410, () -> lifecycle.authorize(lab.id(), "alice", 1, true));
    fails(
        410, () -> lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true));
    sql(
        "UPDATE lab_sessions SET last_activity=now(),expires_at=now()-interval '1 second' WHERE id=?",
        lab.id());
    fails(
        410, () -> lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true));
    lifecycle.disable("alice");
    lifecycle.disable("alice");
    fails(403, () -> lifecycle.get(lab.id(), "alice"));
    fails(403, () -> provision("alice"));
  }

  @Test
  void recoveryPersistsFailureAndRetriesCleanup() {
    Lab lab = provision("alice");
    cluster.failure(true);
    worker.reconcile(lab); // Inject a provisioning failure before the cluster becomes ready.
    assertEquals("infrastructure_error", lifecycle.get(lab.id(), "alice").state());
    assertFalse(lifecycle.get(lab.id(), "alice").cleanupComplete());
    worker.tick();
    assertFalse(lifecycle.get(lab.id(), "alice").cleanupComplete());
  }

  @Test
  void httpRequiresServiceAuthenticationAndValidPayloads() {
    given().get("/q/health/ready").then().statusCode(200);
    given().get("/internal/labs/" + UUID.randomUUID()).then().statusCode(401);
    given()
        .auth()
        .oauth2("wrong")
        .get("/internal/labs/" + UUID.randomUUID())
        .then()
        .statusCode(401);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body("{}")
        .post("/internal/labs")
        .then()
        .statusCode(400);
    UUID id = UUID.randomUUID();
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(
            Map.of(
                "key", UUID.randomUUID(), "id", id, "subject", "alice", "scenario", "foundations"))
        .post("/internal/labs")
        .then()
        .statusCode(200);
    given()
        .auth()
        .oauth2(TOKEN)
        .queryParam("subject", "alice")
        .get("/internal/labs/" + id)
        .then()
        .statusCode(200);
    given()
        .auth()
        .oauth2(TOKEN)
        .queryParam("subject", "other")
        .get("/internal/labs/" + id)
        .then()
        .statusCode(404);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(Map.of("subject", "alice"))
        .post("/internal/accounts/disable")
        .then()
        .statusCode(204);
  }

  @Test
  void restartResetAndTeardownRecovery() {
    Lab lab = provision("restart");
    worker.tick();
    lab = lifecycle.get(lab.id(), "restart");
    assertEquals("ready", lab.state());
    Lab reset = lifecycle.change(UUID.randomUUID(), lab.id(), "restart", lab.revision(), 1, true);
    int before = cluster.removals();
    worker.tick();
    assertTrue(cluster.removals() > before);
    Lab ready = lifecycle.get(lab.id(), "restart");
    assertEquals("ready", ready.state());
    assertEquals(2, ready.generation());
    lifecycle.change(UUID.randomUUID(), ready.id(), "restart", ready.revision(), 2, false);
    cluster.failure(true);
    worker.tick();
    assertFalse(lifecycle.get(lab.id(), "restart").cleanupComplete());
    cluster.failure(false);
    worker.tick();
    Lab closed = lifecycle.get(lab.id(), "restart");
    assertEquals("closed", closed.state());
    assertTrue(closed.cleanupComplete());
    Lab again =
        lifecycle.change(UUID.randomUUID(), closed.id(), "restart", closed.revision(), 2, false);
    assertEquals("closed", again.state());
    worker.tick();
  }

  @Test
  void workerExpiresAbandonedSessionsAndFreesCapacityAfterCleanup() {
    Lab lab = ready("idle");
    sql("UPDATE lab_sessions SET last_activity=now()-interval '601 seconds' WHERE id=?", lab.id());
    worker.tick();
    Lab expired = lifecycle.get(lab.id(), "idle");
    assertEquals("expired", expired.state());
    assertTrue(expired.cleanupComplete());
    provision("next");
    Lab provisioning = provision("deadline");
    sql(
        "UPDATE lab_sessions SET expires_at=now()-interval '1 second' WHERE id=?",
        provisioning.id());
    worker.tick();
    assertEquals("expired", lifecycle.get(provisioning.id(), "deadline").state());
  }

  @Test
  void internalEndpointsValidateAndApplyTicketResetClose() {
    Lab lab = ready("alice");
    String base = "/internal/labs/" + lab.id();
    String ticket = UUID.randomUUID().toString();
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(
            Map.of(
                "subject",
                "alice",
                "generation",
                1,
                "hash",
                Lifecycle.hash(ticket),
                "expiresAt",
                Instant.now().plusSeconds(30).truncatedTo(ChronoUnit.MICROS).toString()))
        .post(base + "/tickets")
        .then()
        .statusCode(204);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body("{}")
        .post(base + "/tickets")
        .then()
        .statusCode(400);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body("{}")
        .post(base + "/reset")
        .then()
        .statusCode(400);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(
            Map.of(
                "key",
                UUID.randomUUID(),
                "subject",
                "alice",
                "revision",
                lab.revision(),
                "generation",
                1))
        .post(base + "/reset")
        .then()
        .statusCode(200);
    Lab reset = lifecycle.get(lab.id(), "alice");
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(
            Map.of(
                "key",
                UUID.randomUUID(),
                "subject",
                "alice",
                "revision",
                reset.revision(),
                "generation",
                2))
        .post(base + "/close")
        .then()
        .statusCode(200);
    given()
        .auth()
        .oauth2(TOKEN)
        .contentType("application/json")
        .body(Map.of("subject", ""))
        .post("/internal/accounts/disable")
        .then()
        .statusCode(400);
  }

  @Test
  void rejectsExpiredTicketAndInvalidIdentityWithoutConsumption() {
    Lab lab = ready("alice");
    String ticket = UUID.randomUUID().toString();
    lifecycle.registerTicket(
        lab.id(), "alice", 1, Lifecycle.hash(ticket), Instant.now().plusSeconds(30));
    sql("UPDATE terminal_tickets SET expires_at=now()-interval '1 second'");
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, ticket));
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, null));
    fails(401, () -> lifecycle.redeem(lab.id(), "alice", 1, "x".repeat(257)));
    fails(400, () -> lifecycle.registerTicket(lab.id(), "alice", 1, null, Instant.now()));
    fails(400, () -> lifecycle.registerTicket(lab.id(), "alice", 1, Lifecycle.hash(ticket), null));
    fails(
        400, () -> lifecycle.provision(UUID.randomUUID(), UUID.randomUUID(), null, "foundations"));
    fails(
        400,
        () ->
            lifecycle.provision(
                UUID.randomUUID(), UUID.randomUUID(), "x".repeat(201), "foundations"));
    sql("UPDATE lab_sessions SET expires_at=now()-interval '1 second' WHERE id=?", lab.id());
    fails(410, () -> lifecycle.authorize(lab.id(), "alice", 1, false));
  }
}
