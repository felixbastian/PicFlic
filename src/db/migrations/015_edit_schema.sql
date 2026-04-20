ALTER TABLE fact_vocabulary
    ADD COLUMN IF NOT EXISTS example_sentences TEXT[] NOT NULL DEFAULT '{}'::TEXT[];
