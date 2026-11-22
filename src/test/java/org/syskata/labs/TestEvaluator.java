package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.annotation.Priority;
import jakarta.enterprise.inject.Alternative;
import jakarta.inject.Singleton;
import java.util.Map;

@Alternative
@Priority(1)
@Singleton
public class TestEvaluator extends Evaluator {
  JsonNode next;
  Runnable during = () -> {};
  int calls;

  @Override
  public JsonNode observe(Lab lab, String operation, JsonNode stage) {
    calls++;
    during.run();
    if (next != null) {
      return next;
    }
    if (operation.equals("stage")) {
      return json.valueToTree(
          Map.of(
              "stage",
              Map.of(
                  "phase",
                  "fault_observed",
                  "uid",
                  "test-deployment",
                  "initialGeneration",
                  1,
                  "faultGeneration",
                  2)));
    }
    return json.valueToTree(
        Map.of(
            "criteria",
            Assessment.criteria(lab.scenario()).stream()
                .map(c -> Map.of("criterion_id", "criterion." + c, "passed", true))
                .toList()));
  }
}
