package org.syskata.labs;

import jakarta.annotation.Priority;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Alternative;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicInteger;

@Alternative
@Priority(1)
@ApplicationScoped
public class TestCluster extends Cluster {
  private boolean failure;
  private final AtomicInteger removals = new AtomicInteger();

  public void failure(boolean value) {
    failure = value;
  }

  public int removals() {
    return removals.get();
  }

  @Override
  public void apply(Lab lab) {
    if (failure) {
      throw new LabFailure(503, "injected_failure");
    }
  }

  @Override
  public void remove(Lab lab, int generation) {
    if (failure) {
      throw new LabFailure(503, "injected_failure");
    }
    removals.incrementAndGet();
  }

  @Override
  public Process terminal(Lab lab) {
    try {
      return new ProcessBuilder("cat").start();
    } catch (IOException failure) {
      throw new IllegalStateException(failure);
    }
  }
}
