package org.syskata.labs;

import jakarta.ws.rs.container.ContainerRequestContext;
import jakarta.ws.rs.container.ContainerRequestFilter;
import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.ext.Provider;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@Provider
public class InternalAuth implements ContainerRequestFilter {
  @ConfigProperty(name = "labs.internal-token")
  String token;

  @Override
  public void filter(ContainerRequestContext context) {
    String supplied = context.getHeaderString("Authorization");
    if (token.length() < 32
        || supplied == null
        || !MessageDigest.isEqual(
            ("Bearer " + token).getBytes(StandardCharsets.UTF_8),
            supplied.getBytes(StandardCharsets.UTF_8))) {
      context.abortWith(Response.status(401).entity(Map.of("code", "unauthorized")).build());
    }
  }
}
