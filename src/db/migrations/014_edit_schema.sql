ALTER TABLE fact_vocab_conversation_sessions
    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Europe/Paris',
    ALTER COLUMN last_activity_at TYPE TIMESTAMPTZ USING last_activity_at AT TIME ZONE 'Europe/Paris',
    ALTER COLUMN timeout_at TYPE TIMESTAMPTZ USING timeout_at AT TIME ZONE 'Europe/Paris',
    ALTER COLUMN completed_at TYPE TIMESTAMPTZ USING completed_at AT TIME ZONE 'Europe/Paris';

ALTER TABLE fact_vocab_conversation_turns
    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Europe/Paris';
