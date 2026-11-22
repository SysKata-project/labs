package org.syskata.labs;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.http.HttpRequest;
import java.nio.file.Files;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class AssessmentBoundaryTest {
  static class Transport extends ResultDelivery {
    final ArrayDeque<Response> responses = new ArrayDeque<>();
    final List<HttpRequest> requests = new ArrayList<>();
    boolean interrupt;

    @Override
    protected Response exchange(HttpRequest request) throws Exception {
      if (interrupt) {
        throw new InterruptedException();
      }
      requests.add(request);
      return responses.remove();
    }
  }

  @Test
  void callbackAuthenticationReceiptAndHttpsValidation() {
    Transport transport = new Transport();
    transport.json = new ObjectMapper();
    transport.client = "test-client";
    transport.secret = "synthetic-only";
    transport.tokenUrl = "https://identity.example.invalid/token";
    transport.base = "https://api.example.invalid";
    UUID id = UUID.randomUUID();
    for (String disposition :
        List.of("applied", "duplicate", "ignored_superseded", "ignored_expired", "unknown")) {
      transport.responses.add(
          new ResultDelivery.Response(
              200, transport.json.valueToTree(Map.of("access_token", "synthetic-only"))));
      transport.responses.add(
          new ResultDelivery.Response(
              200,
              transport.json.valueToTree(
                  Map.of("assessment_id", id.toString(), "disposition", disposition))));
      assertEquals(!disposition.equals("unknown"), transport.send(id, "{}"));
      HttpRequest callback = transport.requests.getLast();
      assertEquals(
          "Bearer synthetic-only", callback.headers().firstValue("Authorization").orElseThrow());
      assertEquals(id.toString(), callback.headers().firstValue("Idempotency-Key").orElseThrow());
      assertEquals("/internal/v1/assessments/" + id + "/result", callback.uri().getPath());
    }
    for (int status : List.of(401, 200)) {
      transport.responses.add(
          new ResultDelivery.Response(status, transport.json.createObjectNode()));
      assertFalse(transport.send(id, "{}"));
    }
    for (String url :
        List.of(
            "http://insecure.invalid",
            "https://user@host.invalid",
            "https://host.invalid?x=1",
            "https://host.invalid#fragment",
            "https:/missing-host")) {
      transport.tokenUrl = url;
      assertFalse(transport.send(id, "{}"));
    }
    transport.tokenUrl = "https://identity.example.invalid/token";
    transport.interrupt = true;
    assertFalse(transport.send(id, "{}"));
    assertTrue(Thread.interrupted());
  }

  @Test
  void adapterBoundsOutputAndDoesNotInheritServiceSecrets() throws Exception {
    var helper = Files.createTempFile("assessment-helper-test", ".py");
    try {
      Evaluator evaluator = new Evaluator();
      evaluator.json = new ObjectMapper();
      evaluator.helper = helper.toString();
      evaluator.kubeconfig = "/nonexistent/synthetic";
      evaluator.domain = "apps.example.invalid";
      Lab lab =
          new Lab(
              UUID.randomUUID(),
              "alice",
              "foundations",
              1,
              1,
              "ready",
              Instant.now().plusSeconds(60),
              Instant.now(),
              false);
      Files.writeString(
          helper,
          "import os,json,sys\nr=json.load(sys.stdin)\nassert 'LAB_INTERNAL_TOKEN' not in os.environ\nassert 'ASSESSMENT_CLIENT_SECRET' not in os.environ\nprint(json.dumps({'lab':r['lab']}))\n");
      assertEquals(
          lab.id().toString(),
          evaluator.observe(lab, "assess", evaluator.json.createObjectNode()).path("lab").asText());
      Files.writeString(helper, "print('x'*16385)\n");
      assertEquals(
          "cluster_unavailable",
          evaluator
              .observe(lab, "assess", evaluator.json.createObjectNode())
              .path("failure_code")
              .asText());
      Files.writeString(helper, "raise SystemExit(1)\n");
      assertTrue(
          evaluator.observe(lab, "assess", evaluator.json.createObjectNode()).has("failure_code"));
      Files.writeString(helper, "pass\n");
      assertTrue(
          evaluator.observe(lab, "assess", evaluator.json.createObjectNode()).has("failure_code"));
      Files.writeString(helper, "print('[]')\n");
      assertTrue(
          evaluator.observe(lab, "assess", evaluator.json.createObjectNode()).has("failure_code"));
      Files.writeString(helper, "print('not json')\n");
      assertTrue(
          evaluator.observe(lab, "assess", evaluator.json.createObjectNode()).has("failure_code"));
    } finally {
      Files.delete(helper);
    }
  }
}
