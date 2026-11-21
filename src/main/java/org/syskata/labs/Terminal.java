package org.syskata.labs;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.quarkus.websockets.next.OnClose;
import io.quarkus.websockets.next.OnOpen;
import io.quarkus.websockets.next.OnTextMessage;
import io.quarkus.websockets.next.WebSocket;
import io.quarkus.websockets.next.WebSocketConnection;
import jakarta.inject.Inject;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

@WebSocket(path = "/terminal/{id}")
public class Terminal {
  @Inject WebSocketConnection connection;
  @Inject Lifecycle lifecycle;
  @Inject Cluster cluster;
  @Inject ObjectMapper json;
  private final Map<String, Link> links = new ConcurrentHashMap<>();
  private final AtomicInteger slots = new AtomicInteger();

  static final class Link {
    final WebSocketConnection socket;
    final Instant deadline = Instant.now().plusSeconds(5);
    volatile Lab lab;
    volatile Process process;
    volatile boolean closed;

    Link(WebSocketConnection socket) {
      this.socket = socket;
    }
  }

  @OnOpen
  public void open(WebSocketConnection actual) {
    // Bound unauthenticated sockets as well as authenticated sessions.
    if (slots.incrementAndGet() > 8) {
      slots.decrementAndGet();
      connection.close().subscribe().with(ignored -> {}, failure -> {});
      return;
    }
    Link link = new Link(actual);
    links.put(connection.id(), link);
    Thread.ofVirtual().start(() -> monitor(link));
  }

  @OnTextMessage
  public void message(String message) {
    Link link = links.get(connection.id());
    if (link == null) {
      return;
    }
    try {
      var frame = json.readTree(message);
      if (link.lab == null) {
        authenticate(link, frame);
      } else {
        input(link, frame);
      }
    } catch (Exception failure) {
      end(link);
    }
  }

  private void authenticate(Link link, com.fasterxml.jackson.databind.JsonNode frame) {
    if (!Instant.now().isBefore(link.deadline) || !frame.path("type").asText().equals("auth")) {
      throw new LabFailure(401, "authentication_required");
    }
    Lab lab =
        lifecycle.redeem(
            UUID.fromString(connection.pathParam("id")),
            frame.path("subject").asText(),
            frame.path("generation").asInt(),
            frame.path("ticket").asText());
    link.lab = lab;
    link.process = cluster.terminal(lab);
    if (link.closed) {
      stopProcess(link.process);
      return;
    }
    Thread.ofVirtual().start(() -> output(link));
  }

  private void input(Link link, com.fasterxml.jackson.databind.JsonNode frame)
      throws java.io.IOException {
    if (!frame.path("type").asText().equals("stdin")) {
      throw new LabFailure(400, "invalid_frame");
    }
    lifecycle.authorize(link.lab.id(), link.lab.subject(), link.lab.generation(), true);
    byte[] input = Base64.getDecoder().decode(frame.path("data").asText());
    if (input.length > 4096) {
      throw new LabFailure(400, "frame_too_large");
    }
    link.process.getOutputStream().write(input);
    link.process.getOutputStream().flush();
  }

  private void output(Link link) {
    try (var input = link.process.getInputStream()) {
      byte[] buffer = new byte[4096];
      int count;
      while (!link.closed && (count = input.read(buffer)) != -1) {
        // Backpressure: do not read more until this bounded frame has been delivered.
        byte[] chunk = java.util.Arrays.copyOf(buffer, count);
        link.socket
            .sendText(
                json.writeValueAsString(
                    Map.of("type", "stdout", "data", Base64.getEncoder().encodeToString(chunk))))
            .await()
            .atMost(java.time.Duration.ofSeconds(5));
      }
    } catch (Exception failure) {
      end(link);
    } finally {
      end(link);
    }
  }

  private void monitor(Link link) {
    try {
      while (!link.closed) {
        Thread.sleep(200);
        Lab lab = link.lab;
        if (lab == null) {
          if (!Instant.now().isBefore(link.deadline)) {
            end(link);
          }
        } else {
          lifecycle.authorize(lab.id(), lab.subject(), lab.generation(), false);
        }
      }
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      end(link);
    } catch (LabFailure failure) {
      end(link);
    }
  }

  @OnClose
  public void close() {
    Link link = links.get(connection.id());
    if (link != null) {
      end(link);
    }
  }

  private void stopProcess(Process process) {
    process.descendants().forEach(ProcessHandle::destroy);
    process.destroy();
  }

  private void end(Link link) {
    synchronized (link) {
      if (link.closed) {
        return;
      }
      link.closed = true;
      links.remove(link.socket.id());
      slots.decrementAndGet();
      if (link.process != null) {
        stopProcess(link.process);
      }
      link.socket.close().subscribe().with(ignored -> {}, failure -> {});
    }
  }
}
