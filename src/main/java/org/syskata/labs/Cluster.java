package org.syskata.labs;

import jakarta.enterprise.context.ApplicationScoped;
import java.io.IOException;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class Cluster {
  @ConfigProperty(name = "labs.kubeconfig")
  String kubeconfig;

  @ConfigProperty(name = "labs.cluster-helper")
  String helper;

  @ConfigProperty(name = "labs.terminal-image")
  String image;

  @ConfigProperty(name = "labs.api-cidr")
  String apiCidr;

  @ConfigProperty(name = "labs.route-domain")
  String routeDomain;

  protected Process start(String operation, Lab lab, int generation) throws IOException {
    ProcessBuilder builder =
        new ProcessBuilder(
            List.of(
                "python3",
                helper,
                operation,
                lab.id().toString(),
                Integer.toString(generation),
                lab.scenario()));
    builder.environment().put("KUBECONFIG", kubeconfig);
    builder.environment().put("LAB_TERMINAL_IMAGE", image);
    builder.environment().put("LAB_API_CIDR", apiCidr);
    builder.environment().put("LAB_ROUTE_DOMAIN", routeDomain);
    builder.redirectError(ProcessBuilder.Redirect.DISCARD);
    return builder.start();
  }

  public void apply(Lab lab) {
    run("provision", lab, lab.generation());
  }

  public void remove(Lab lab, int generation) {
    run("close", lab, generation);
  }

  private void run(String operation, Lab lab, int generation) {
    Process process = null;
    try {
      process = start(operation, lab, generation);
      process.getOutputStream().close();
      discardOutput(process);
      if (!process.waitFor(240, TimeUnit.SECONDS) || process.exitValue() != 0) {
        throw new LabFailure(503, "cluster_unavailable");
      }
    } catch (IOException failure) {
      throw new LabFailure(503, "cluster_unavailable");
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      throw new LabFailure(503, "cluster_interrupted");
    } finally {
      stop(process);
    }
  }

  private void stop(Process process) {
    if (process != null && process.isAlive()) {
      process.descendants().forEach(ProcessHandle::destroy);
      process.destroyForcibly();
    }
  }

  private void discardOutput(Process process) {
    Thread.ofVirtual()
        .start(
            () -> {
              try (var input = process.getInputStream()) {
                input.transferTo(java.io.OutputStream.nullOutputStream());
              } catch (IOException failure) {
                process.destroyForcibly();
              }
            });
  }

  public Process terminal(Lab lab) {
    try {
      return start("terminal", lab, lab.generation());
    } catch (IOException failure) {
      throw new LabFailure(503, "terminal_unavailable");
    }
  }
}
