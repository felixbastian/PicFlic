ALTER TABLE dim_user
ADD COLUMN IF NOT EXISTS has_vocab_conversation_bot_activated BOOLEAN NOT NULL DEFAULT FALSE;
