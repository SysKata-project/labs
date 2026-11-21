package org.syskata.labs;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.sql.DataSource;

@ApplicationScoped
public class Store {
  @Inject DataSource source;
  @Inject ObjectMapper json;

  @FunctionalInterface
  public interface Work<T> {
    T run(Connection connection) throws Exception;
  }

  public <T> T transaction(Work<T> work) {
    try (Connection connection = source.getConnection()) {
      connection.setAutoCommit(false);
      try {
        T result = work.run(connection);
        connection.commit();
        return result;
      } catch (Exception failure) {
        connection.rollback();
        if (failure instanceof LabFailure known) {
          throw known;
        }
        throw new LabFailure(503, "storage_unavailable");
      }
    } catch (SQLException failure) {
      throw new LabFailure(503, "storage_unavailable");
    }
  }

  public int update(Connection connection, String sql, Object... args) throws SQLException {
    try (PreparedStatement statement = prepare(connection, sql, args)) {
      return statement.executeUpdate();
    }
  }

  public PreparedStatement prepare(Connection connection, String sql, Object... args)
      throws SQLException {
    PreparedStatement statement = connection.prepareStatement(sql);
    for (int i = 0; i < args.length; i++) {
      Object value = args[i];
      statement.setObject(
          i + 1, value instanceof Instant instant ? Timestamp.from(instant) : value);
    }
    return statement;
  }

  public void lock(Connection connection) throws SQLException {
    try (PreparedStatement statement =
            prepare(connection, "SELECT id FROM admission_lock WHERE id=1 FOR UPDATE");
        ResultSet rows = statement.executeQuery()) {
      if (!rows.next()) {
        throw new LabFailure(503, "admission_unavailable");
      }
    }
  }

  public boolean blocked(Connection connection, String subject) throws SQLException {
    try (PreparedStatement statement =
            prepare(connection, "SELECT subject FROM blocked_subjects WHERE subject=?", subject);
        ResultSet rows = statement.executeQuery()) {
      return rows.next();
    }
  }

  public Lab get(Connection connection, UUID id) throws SQLException {
    try (PreparedStatement statement =
            prepare(connection, "SELECT * FROM lab_sessions WHERE id=? FOR UPDATE", id);
        ResultSet rows = statement.executeQuery()) {
      if (!rows.next()) {
        throw new LabFailure(404, "lab_not_found");
      }
      return read(rows);
    }
  }

  public List<Lab> all(Connection connection) throws SQLException {
    List<Lab> labs = new ArrayList<>();
    try (PreparedStatement statement =
            prepare(connection, "SELECT * FROM lab_sessions ORDER BY id");
        ResultSet rows = statement.executeQuery()) {
      while (rows.next()) {
        labs.add(read(rows));
      }
    }
    return labs;
  }

  public Lab replay(Connection connection, UUID key, String fingerprint) throws Exception {
    try (PreparedStatement statement =
            prepare(
                connection, "SELECT fingerprint,response::text FROM operations WHERE key=?", key);
        ResultSet rows = statement.executeQuery()) {
      if (!rows.next()) {
        return null;
      }
      if (!fingerprint.equals(rows.getString(1))) {
        throw new LabFailure(409, "idempotency_conflict");
      }
      return json.readValue(rows.getString(2), Lab.class);
    }
  }

  public Lab remember(Connection connection, UUID key, String fingerprint, Lab lab)
      throws Exception {
    update(
        connection,
        "INSERT INTO operations VALUES (?,?,?::jsonb)",
        key,
        fingerprint,
        json.writeValueAsString(lab));
    return lab;
  }

  private Lab read(ResultSet row) throws SQLException {
    return new Lab(
        row.getObject("id", UUID.class),
        row.getString("subject"),
        row.getString("scenario"),
        row.getInt("generation"),
        row.getLong("revision"),
        row.getString("state"),
        row.getTimestamp("expires_at").toInstant(),
        row.getTimestamp("last_activity").toInstant(),
        row.getBoolean("cleanup_complete"));
  }
}
