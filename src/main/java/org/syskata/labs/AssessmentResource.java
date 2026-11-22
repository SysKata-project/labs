package org.syskata.labs;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import java.util.UUID;

@Path("/internal")
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public class AssessmentResource {
  @Inject Assessment assessments;

  @POST
  @Path("/assessments")
  public JsonNode submit(Assessment.Dispatch request) {
    return assessments.submit(request);
  }

  @GET
  @Path("/assessments/{id}")
  public JsonNode get(@PathParam("id") UUID id, @QueryParam("subject") String subject) {
    return assessments.get(id, subject);
  }

  public record Stage(String subject, int generation) {}

  @POST
  @Path("/labs/{id}/fault-stage")
  public JsonNode stage(@PathParam("id") UUID id, Stage request) {
    if (request == null) {
      throw new LabFailure(400, "invalid_request");
    }
    return assessments.stage(id, request.subject(), request.generation(), true);
  }

  @GET
  @Path("/labs/{id}/fault-stage")
  public JsonNode stageStatus(
      @PathParam("id") UUID id,
      @QueryParam("subject") String subject,
      @QueryParam("generation") int generation) {
    return assessments.stage(id, subject, generation, false);
  }
}
