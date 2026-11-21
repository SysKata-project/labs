package org.syskata.labs;

import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import java.time.Instant;
import java.util.UUID;

@Path("/internal")
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public class InternalResource {
  @Inject Lifecycle lifecycle;

  public record Provision(UUID key, UUID id, String subject, String scenario) {}

  public record Change(UUID key, String subject, long revision, int generation) {}

  public record Ticket(String subject, int generation, String hash, Instant expiresAt) {}

  public record Subject(String subject) {}

  @POST
  @Path("/labs")
  public Lab provision(Provision request) {
    required(
        request != null
            && request.key() != null
            && request.id() != null
            && request.scenario() != null);
    return lifecycle.provision(request.key(), request.id(), request.subject(), request.scenario());
  }

  @GET
  @Path("/labs/{id}")
  public Lab get(@PathParam("id") UUID id, @QueryParam("subject") String subject) {
    return lifecycle.get(id, subject);
  }

  @POST
  @Path("/labs/{id}/reset")
  public Lab reset(@PathParam("id") UUID id, Change request) {
    return change(id, request, true);
  }

  @POST
  @Path("/labs/{id}/close")
  public Lab close(@PathParam("id") UUID id, Change request) {
    return change(id, request, false);
  }

  private Lab change(UUID id, Change request, boolean reset) {
    required(request != null && request.key() != null && request.subject() != null);
    return lifecycle.change(
        request.key(), id, request.subject(), request.revision(), request.generation(), reset);
  }

  @POST
  @Path("/labs/{id}/tickets")
  public void ticket(@PathParam("id") UUID id, Ticket request) {
    required(request != null && request.subject() != null);
    lifecycle.registerTicket(
        id, request.subject(), request.generation(), request.hash(), request.expiresAt());
  }

  @POST
  @Path("/accounts/disable")
  public void disable(Subject request) {
    required(request != null && request.subject() != null && !request.subject().isBlank());
    lifecycle.disable(request.subject());
  }

  private void required(boolean condition) {
    if (!condition) {
      throw new LabFailure(400, "invalid_request");
    }
  }
}
