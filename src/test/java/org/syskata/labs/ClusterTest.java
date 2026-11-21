package org.syskata.labs;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ClusterTest {
  private Lab lab() {
    return new Lab(
        UUID.randomUUID(),
        "test",
        "foundations",
        1,
        1,
        "ready",
        Instant.now().plusSeconds(60),
        Instant.now(),
        false);
  }

  @Test
  void adapterHandlesSuccessFailureAndMissingExecutable() throws Exception {
    Cluster ok =
        new Cluster() {
          @Override
          protected Process start(String operation, Lab lab, int generation) throws IOException {
            return new ProcessBuilder("true").start();
          }
        };
    ok.apply(lab());
    ok.remove(lab(), 1);
    assertEquals(0, ok.terminal(lab()).waitFor());
    Cluster failed =
        new Cluster() {
          @Override
          protected Process start(String operation, Lab lab, int generation) throws IOException {
            return new ProcessBuilder("false").start();
          }
        };
    assertThrows(LabFailure.class, () -> failed.apply(lab()));
    Cluster missing =
        new Cluster() {
          @Override
          protected Process start(String operation, Lab lab, int generation) throws IOException {
            throw new IOException("synthetic failure");
          }
        };
    assertThrows(LabFailure.class, () -> missing.remove(lab(), 1));
    assertThrows(LabFailure.class, () -> missing.terminal(lab()));
  }
}
