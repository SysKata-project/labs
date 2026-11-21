package org.syskata.labs;

public final class LabFailure extends RuntimeException {
  private final int status;

  public LabFailure(int status, String code) {
    super(code);
    this.status = status;
  }

  public int status() {
    return status;
  }
}
