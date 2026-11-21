CREATE TABLE admission_lock (id integer PRIMARY KEY CHECK (id = 1));
INSERT INTO admission_lock VALUES (1);
CREATE TABLE blocked_subjects (subject text PRIMARY KEY);
CREATE TABLE lab_sessions (
 id uuid PRIMARY KEY, subject text NOT NULL, scenario text NOT NULL,
 generation integer NOT NULL CHECK (generation > 0), revision bigint NOT NULL,
 state text NOT NULL CHECK (state IN ('provisioning','ready','resetting','closing','closed','expired','infrastructure_error')),
 expires_at timestamptz NOT NULL, last_activity timestamptz NOT NULL,
 cleanup_complete boolean NOT NULL DEFAULT false
);
CREATE TABLE operations (
 key uuid PRIMARY KEY, fingerprint text NOT NULL, response jsonb NOT NULL
);
CREATE TABLE terminal_tickets (
 hash text PRIMARY KEY, session_id uuid NOT NULL REFERENCES lab_sessions(id),
 subject text NOT NULL, generation integer NOT NULL, expires_at timestamptz NOT NULL,
 consumed boolean NOT NULL DEFAULT false
);
CREATE TABLE generations (
 session_id uuid NOT NULL REFERENCES lab_sessions(id), generation integer NOT NULL,
 PRIMARY KEY (session_id,generation)
);
