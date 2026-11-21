package org.syskata.labs;

import io.quarkus.scheduler.Scheduled;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class Reconciler {
  @Inject Store store;
  @Inject javax.sql.DataSource source;
  @Inject Cluster cluster;

  @ConfigProperty(name = "labs.worker-enabled")
  boolean enabled;

  @Scheduled(every = "2s", concurrentExecution = Scheduled.ConcurrentExecution.SKIP)
  void scheduled() {
    if (enabled) {
      tick();
    }
  }

  public void tick() {
    // One database advisory lock prevents multiple controller replicas applying stale work.
    // Session intent remains durable; loss of the connection releases this lock.
    try (var connection = source.getConnection();
        var lock = connection.prepareStatement("SELECT pg_try_advisory_lock(742193)");
        var result = lock.executeQuery()) {
      if (result.next() && result.getBoolean(1)) {
        try {
          for (Lab lab : store.transaction(store::all)) {
            reconcile(lab);
          }
        } finally {
          try (var unlock = connection.prepareStatement("SELECT pg_advisory_unlock(742193)")) {
            unlock.execute();
          }
        }
      }
    } catch (Exception failure) {
      // Retry on the next tick; never log driver/cluster output or mark teardown complete.
      System.getLogger(Reconciler.class.getName())
          .log(System.Logger.Level.WARNING, "Lab reconciliation will retry");
    }
  }

  public void reconcile(Lab observed) {
    Lab lab = expire(observed);
    if (lab.cleanupComplete() || lab.state().equals("ready")) {
      return;
    }
    try {
      cleanup(lab);
      if (lab.state().equals("provisioning") || lab.state().equals("resetting")) {
        cluster.apply(lab);
        store.transaction(
            connection -> {
              store.update(
                  connection,
                  "UPDATE lab_sessions SET state='ready',revision=revision+1,last_activity=now() WHERE id=? AND generation=? AND state=?",
                  lab.id(),
                  lab.generation(),
                  lab.state());
              return null;
            });
      } else {
        store.transaction(
            connection -> {
              store.update(
                  connection,
                  "UPDATE lab_sessions SET cleanup_complete=true,state=CASE WHEN state='closing' THEN 'closed' ELSE state END WHERE id=? AND generation=? AND state=?",
                  lab.id(),
                  lab.generation(),
                  lab.state());
              return null;
            });
      }
    } catch (LabFailure failure) {
      store.transaction(
          connection -> {
            store.update(
                connection,
                "UPDATE lab_sessions SET state='infrastructure_error',revision=revision+1 WHERE id=? AND generation=? AND state IN ('provisioning','resetting')",
                lab.id(),
                lab.generation());
            return null;
          });
    }
  }

  private Lab expire(Lab observed) {
    return store.transaction(
        connection -> {
          Lab lab = store.get(connection, observed.id());
          Instant now = Instant.now();
          if (!lab.cleanupComplete()
              && !List.of("closing", "closed", "expired", "infrastructure_error")
                  .contains(lab.state())
              && (!now.isBefore(lab.expiresAt())
                  || (lab.state().equals("ready")
                      && !now.isBefore(lab.lastActivity().plusSeconds(600))))) {
            store.update(
                connection,
                "UPDATE lab_sessions SET state='expired',revision=revision+1 WHERE id=?",
                lab.id());
            store.update(connection, "DELETE FROM terminal_tickets WHERE session_id=?", lab.id());
          }
          return store.get(connection, observed.id());
        });
  }

  private void cleanup(Lab lab) {
    List<Integer> generations =
        store.transaction(
            connection -> {
              List<Integer> values = new ArrayList<>();
              try (PreparedStatement query =
                      store.prepare(
                          connection,
                          "SELECT generation FROM generations WHERE session_id=? ORDER BY generation",
                          lab.id());
                  ResultSet rows = query.executeQuery()) {
                while (rows.next()) {
                  values.add(rows.getInt(1));
                }
              }
              return values;
            });
    boolean provisioning = lab.state().equals("provisioning") || lab.state().equals("resetting");
    for (int generation : generations) {
      if (provisioning && generation == lab.generation()) {
        continue;
      }
      cluster.remove(lab, generation);
      store.transaction(
          connection -> {
            store.update(
                connection,
                "DELETE FROM generations WHERE session_id=? AND generation=?",
                lab.id(),
                generation);
            return null;
          });
    }
  }
}
