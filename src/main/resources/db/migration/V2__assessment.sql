CREATE TABLE assessments (
 id uuid PRIMARY KEY, lab_id uuid NOT NULL REFERENCES lab_sessions(id),
 generation integer NOT NULL, request jsonb NOT NULL,
 state text NOT NULL DEFAULT 'pending', result jsonb,
 delivered boolean NOT NULL DEFAULT false, attempts integer NOT NULL DEFAULT 0,
 next_delivery timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (state IN ('pending','evaluated','infrastructure_error','superseded','expired'))
);
CREATE UNIQUE INDEX one_pending_assessment ON assessments(lab_id,generation) WHERE state='pending';
CREATE TABLE assessment_stages (
 lab_id uuid NOT NULL REFERENCES lab_sessions(id), generation integer NOT NULL,
 evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
 state text NOT NULL DEFAULT 'pending',
 PRIMARY KEY(lab_id,generation), CHECK (state IN ('pending','complete','infrastructure_error','superseded'))
);
