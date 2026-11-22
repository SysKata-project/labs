package org.syskata.labs;

import jakarta.annotation.Priority;
import jakarta.enterprise.inject.Alternative;
import jakarta.inject.Singleton;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Alternative
@Priority(1)
@Singleton
public class TestDelivery extends ResultDelivery {
  boolean accept;
  final List<String> received = new ArrayList<>();

  @Override
  protected boolean send(UUID id, String result) {
    received.add(result);
    return accept;
  }
}
