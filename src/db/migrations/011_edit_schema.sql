ALTER TABLE fact_vocabulary
    ADD COLUMN IF NOT EXISTS number_of_usages_by_conversation_trainer INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS fact_vocab_conversation_sessions (
    conversation_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    telegram_user_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    story_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    user_turn_count INTEGER NOT NULL DEFAULT 0,
    max_user_turns INTEGER NOT NULL DEFAULT 5,
    turn_count INTEGER NOT NULL DEFAULT 0,
    selected_vocabulary_ids TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    last_activity_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    timeout_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP + INTERVAL '36 hours',
    completed_at TIMESTAMP,

    CONSTRAINT fk_vocab_conversation_session_user
    FOREIGN KEY (user_id) REFERENCES dim_user(user_id)
);

CREATE INDEX IF NOT EXISTS idx_vocab_conversation_sessions_user
    ON fact_vocab_conversation_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_vocab_conversation_sessions_status_timeout
    ON fact_vocab_conversation_sessions(status, timeout_at);

CREATE TABLE IF NOT EXISTS fact_vocab_conversation_turns (
    conversation_turn_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    turn_index INTEGER NOT NULL,
    turn_type TEXT NOT NULL,
    text TEXT NOT NULL,
    used_vocabulary_ids TEXT[] NOT NULL DEFAULT '{}'::TEXT[],

    CONSTRAINT fk_vocab_conversation_turn_session
    FOREIGN KEY (conversation_id) REFERENCES fact_vocab_conversation_sessions(conversation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_vocab_conversation_turns_conversation
    ON fact_vocab_conversation_turns(conversation_id, turn_index);
