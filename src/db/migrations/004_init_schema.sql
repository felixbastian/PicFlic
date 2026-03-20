CREATE TABLE fact_receipts (
    user_id UUID PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_name TEXT,
    last_name TEXT
);
