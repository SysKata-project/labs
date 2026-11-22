package org.syskata.labs;

import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

@QuarkusTest
class TerminalTest {
  @Inject Lifecycle lifecycle;
  @Inject Store store;
  @Inject Reconciler worker;
  @Inject TestCluster cluster;

  static class Listener implements WebSocket.Listener {
    final CompletableFuture<String> output = new CompletableFuture<>();
    final CompletableFuture<Integer> close = new CompletableFuture<>();

    @Override
    public void onOpen(WebSocket socket) {
      socket.request(1);
    }

    @Override
    public CompletionStage<?> onText(WebSocket socket, CharSequence data, boolean last) {
      output.complete(data.toString());
      socket.request(1);
      return null;
    }

    @Override
    public CompletionStage<?> onClose(WebSocket socket, int status, String reason) {
      close.complete(status);
      return null;
    }
  }

  @BeforeEach
  void clear() {
    cluster.failure(false);
    store.transaction(
        connection -> {
          store.update(
              connection,
              "TRUNCATE assessments,assessment_stages,operations,terminal_tickets,generations,lab_sessions,blocked_subjects");
          return null;
        });
  }

  Lab ready() {
    Lab lab = lifecycle.provision(UUID.randomUUID(), UUID.randomUUID(), "alice", "foundations");
    worker.tick();
    return lifecycle.get(lab.id(), "alice");
  }

  WebSocket connect(Lab lab, Listener listener) {
    return HttpClient.newHttpClient()
        .newWebSocketBuilder()
        .buildAsync(URI.create("ws://localhost:8081/terminal/" + lab.id()), listener)
        .join();
  }

  String auth(Lab lab) {
    String ticket = UUID.randomUUID().toString();
    lifecycle.registerTicket(
        lab.id(),
        "alice",
        lab.generation(),
        Lifecycle.hash(ticket),
        Instant.now().plusSeconds(45).truncatedTo(ChronoUnit.MICROS));
    return "{\"type\":\"auth\",\"subject\":\"alice\",\"generation\":"
        + lab.generation()
        + ",\"ticket\":\""
        + ticket
        + "\"}";
  }

  @Test
  void streamsThenRevokesOnResetAndRejectsReplay() throws Exception {
    Lab lab = ready();
    Listener listener = new Listener();
    WebSocket socket = connect(lab, listener);
    String auth = auth(lab);
    socket.sendText(auth, true).join();
    socket.sendText("{\"type\":\"stdin\",\"data\":\"aGVsbG8K\"}", true).join();
    assertTrue(listener.output.get(5, TimeUnit.SECONDS).contains("aGVsbG8K"));
    Listener replay = new Listener();
    connect(lab, replay).sendText(auth, true).join();
    replay.close.get(5, TimeUnit.SECONDS);
    lifecycle.change(UUID.randomUUID(), lab.id(), "alice", lab.revision(), 1, true);
    listener.close.get(5, TimeUnit.SECONDS);
  }

  @Test
  void closesWithoutAuthenticationAndOnBadFrames() throws Exception {
    Lab lab = ready();
    Listener timeout = new Listener();
    connect(lab, timeout);
    timeout.close.get(7, TimeUnit.SECONDS);
    Listener bad = new Listener();
    connect(lab, bad).sendText("{}", true).join();
    bad.close.get(5, TimeUnit.SECONDS);
  }

  @Test
  void disabledAccountTerminatesActiveStream() throws Exception {
    Lab lab = ready();
    Listener listener = new Listener();
    WebSocket socket = connect(lab, listener);
    socket.sendText(auth(lab), true).join();
    socket.sendText("{\"type\":\"stdin\",\"data\":\"aGkK\"}", true).join();
    listener.output.get(5, TimeUnit.SECONDS);
    lifecycle.disable("alice");
    listener.close.get(5, TimeUnit.SECONDS);
  }
}
