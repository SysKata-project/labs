package org.syskata.labs;

import java.time.Instant;
import java.util.UUID;

public record Lab(
    UUID id,
    String subject,
    String scenario,
    int generation,
    long revision,
    String state,
    Instant expiresAt,
    Instant lastActivity,
    boolean cleanupComplete) {
  public boolean usable(Instant now) {
    return state.equals("ready")
        && now.isBefore(expiresAt)
        && now.isBefore(lastActivity.plusSeconds(600));
  }
}
