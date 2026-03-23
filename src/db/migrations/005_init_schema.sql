CREATE TABLE fact_vocabulary (
    vocabulary_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    french_word TEXT,
    english_description TEXT,
    correct_day BOOLEAN DEFAULT FALSE,
    correct_three_days BOOLEAN DEFAULT FALSE,
    correct_week BOOLEAN DEFAULT FALSE,
    correct_month BOOLEAN DEFAULT FALSE,
    finished BOOLEAN DEFAULT FALSE,
    shelf BOOLEAN DEFAULT FALSE,
    current_review_stage TEXT DEFAULT 'day',
    next_review_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '1 day',
    awaiting_review BOOLEAN DEFAULT FALSE,
    last_review_prompted_at TIMESTAMP,

    CONSTRAINT fk_vocabulary_user
    FOREIGN KEY (user_id) REFERENCES dim_user(user_id)
);

CREATE INDEX idx_vocabulary_user ON fact_vocabulary(user_id);
