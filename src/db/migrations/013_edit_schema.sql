ALTER TABLE fact_vocab_conversation_sessions
    ALTER COLUMN timeout_at SET DEFAULT CURRENT_TIMESTAMP + INTERVAL '23 hours';

UPDATE fact_vocab_conversation_sessions
SET timeout_at = LEAST(timeout_at, created_at + INTERVAL '23 hours')
WHERE status = 'active';
