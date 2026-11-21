package org.syskata.labs;

import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.ext.ExceptionMapper;
import jakarta.ws.rs.ext.Provider;
import java.util.Map;

@Provider
public class FailureMapper implements ExceptionMapper<LabFailure> {
  @Override
  public Response toResponse(LabFailure failure) {
    return Response.status(failure.status())
        .entity(Map.of("code", failure.getMessage(), "retryable", failure.status() == 503))
        .build();
  }
}
