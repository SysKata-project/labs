package org.syskata.labs;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Set;
import java.util.UUID;

@ApplicationScoped
public class Lifecycle {
  @Inject Store store;

  public Lab provision(UUID key, UUID id, String subject, String scenario) {
    if (subject == null
        || subject.isBlank()
        || subject.length() > 200
        || !Set.of("foundations", "administration").contains(scenario)) {
      throw new LabFailure(400, "invalid_request");
    }
    String fingerprint = hash("provision\n" + id + "\n" + subject + "\n" + scenario);
    return store.transaction(
        connection -> {
          store.lock(connection);
          requireAccount(connection, subject);
          Lab replay = store.replay(connection, key, fingerprint);
          if (replay != null) {
            return replay;
          }
          if (store.all(connection).stream().anyMatch(lab -> lab.id().equals(id))) {
            throw new LabFailure(409, "lab_already_exists");
          }
          if (store.all(connection).stream().filter(lab -> !lab.cleanupComplete()).count() >= 2) {
            throw new LabFailure(429, "capacity_exhausted");
          }
          Instant now = Instant.now();
          store.update(
              connection,
              "INSERT INTO lab_sessions VALUES (?,?,?,1,1,'provisioning',?,?,false)",
              id,
              subject,
              scenario,
              now.plusSeconds(1800),
              now);
          store.update(connection, "INSERT INTO generations VALUES (?,1)", id);
          return store.remember(connection, key, fingerprint, store.get(connection, id));
        });
  }

  public Lab get(UUID id, String subject) {
    return store.transaction(connection -> owned(connection, id, subject));
  }

  public Lab change(
      UUID key, UUID id, String subject, long revision, int generation, boolean reset) {
    String fingerprint =
        hash((reset ? "reset" : "close") + id + subject + ":" + revision + ":" + generation);
    return store.transaction(
        connection -> {
          store.lock(connection);
          Lab current = owned(connection, id, subject);
          Lab replay = store.replay(connection, key, fingerprint);
          if (replay != null) {
            return replay;
          }
          if (current.revision() != revision || current.generation() != generation) {
            throw new LabFailure(409, "stale_lab");
          }
          if (reset) {
            requireReset(current);
          }
          String next = reset ? "resetting" : "closing";
          if (!reset && current.cleanupComplete()) {
            next = "closed";
          }
          store.update(
              connection,
              "UPDATE lab_sessions SET state=?,generation=?,revision=revision+1 WHERE id=?",
              next,
              generation + (reset ? 1 : 0),
              id);
          store.update(connection, "DELETE FROM terminal_tickets WHERE session_id=?", id);
          if (reset) {
            store.update(connection, "INSERT INTO generations VALUES (?,?)", id, generation + 1);
          }
          return store.remember(connection, key, fingerprint, store.get(connection, id));
        });
  }

  private void requireReset(Lab lab) {
    if (!Instant.now().isBefore(lab.expiresAt())
        || !Instant.now().isBefore(lab.lastActivity().plusSeconds(600))) {
      throw new LabFailure(410, "lab_expired");
    }
    if (!lab.state().equals("ready")) {
      throw new LabFailure(409, "invalid_transition");
    }
  }

  public void disable(String subject) {
    store.transaction(
        connection -> {
          store.lock(connection);
          store.update(
              connection,
              "INSERT INTO blocked_subjects VALUES (?) ON CONFLICT DO NOTHING",
              subject);
          store.update(
              connection,
              "UPDATE lab_sessions SET state='closing',revision=revision+1 WHERE subject=? AND NOT cleanup_complete",
              subject);
          store.update(connection, "DELETE FROM terminal_tickets WHERE subject=?", subject);
          return null;
        });
  }

  public void registerTicket(
      UUID id, String subject, int generation, String hash, Instant expiresAt) {
    if (hash == null || !hash.matches("[a-f0-9]{64}") || expiresAt == null) {
      throw new LabFailure(400, "invalid_ticket");
    }
    store.transaction(
        connection -> {
          store.lock(connection);
          Lab lab = owned(connection, id, subject);
          requireReady(lab, generation);
          Instant now = Instant.now();
          if (!expiresAt.isAfter(now)
              || expiresAt.isAfter(now.plusSeconds(60))
              || expiresAt.isAfter(lab.expiresAt())) {
            throw new LabFailure(400, "invalid_ticket_expiry");
          }
          // The API owns issuance/idempotent plaintext delivery. Only its SHA-256 hash is stored
          // here.
          try (PreparedStatement query =
                  store.prepare(connection, "SELECT * FROM terminal_tickets WHERE hash=?", hash);
              ResultSet row = query.executeQuery()) {
            if (row.next()) {
              if (!row.getObject("session_id", UUID.class).equals(id)
                  || !row.getString("subject").equals(subject)
                  || row.getInt("generation") != generation
                  || !row.getTimestamp("expires_at").toInstant().equals(expiresAt)) {
                throw new LabFailure(409, "ticket_conflict");
              }
              return null;
            }
          }
          store.update(
              connection,
              "INSERT INTO terminal_tickets VALUES (?,?,?,?,?,false)",
              hash,
              id,
              subject,
              generation,
              expiresAt);
          return null;
        });
  }

  public Lab redeem(UUID id, String subject, int generation, String ticket) {
    if (ticket == null || ticket.length() < 32 || ticket.length() > 256) {
      throw new LabFailure(401, "invalid_ticket");
    }
    return store.transaction(
        connection -> {
          store.lock(connection);
          Lab lab = owned(connection, id, subject);
          requireReady(lab, generation);
          int count =
              store.update(
                  connection,
                  "UPDATE terminal_tickets SET consumed=true WHERE hash=? AND session_id=? AND subject=? AND generation=? AND NOT consumed AND expires_at>now()",
                  hash(ticket),
                  id,
                  subject,
                  generation);
          if (count != 1) {
            throw new LabFailure(401, "invalid_ticket");
          }
          return lab;
        });
  }

  public Lab authorize(UUID id, String subject, int generation, boolean activity) {
    return store.transaction(
        connection -> {
          Lab lab = owned(connection, id, subject);
          requireReady(lab, generation);
          if (activity) {
            store.update(connection, "UPDATE lab_sessions SET last_activity=now() WHERE id=?", id);
          }
          return lab;
        });
  }

  private Lab owned(Connection connection, UUID id, String subject) throws Exception {
    requireAccount(connection, subject);
    Lab lab = store.get(connection, id);
    if (!lab.subject().equals(subject)) {
      throw new LabFailure(404, "lab_not_found");
    }
    return lab;
  }

  private void requireAccount(Connection connection, String subject) throws Exception {
    if (store.blocked(connection, subject)) {
      throw new LabFailure(403, "account_disabled");
    }
  }

  private void requireReady(Lab lab, int generation) {
    if (!lab.usable(Instant.now())) {
      throw new LabFailure(410, "lab_unavailable");
    }
    if (lab.generation() != generation) {
      throw new LabFailure(409, "stale_generation");
    }
  }

  public static String hash(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException("SHA-256 unavailable", impossible);
    }
  }
}
