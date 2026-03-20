CREATE TYPE expense_category AS ENUM (
    'Lebensmitteleinkäufe',
    'Kleidung',
    'Dm / Rossmann',
    'Mobilität',
    'Mensa',
    'Bäcker',
    'Taxi / Einzelfahrkarten',
    'Entertainment',
    'Ausgehen (Restaurant / Bar / Kino etc.)',
    'Sonstige',
    'Geschenke',
    'Reisen'
);

CREATE TABLE fact_expenses (
    expense_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    expense_total_amount_in_euros NUMERIC(10, 2),
    category expense_category,

    CONSTRAINT fk_expenses_user
    FOREIGN KEY (user_id) REFERENCES dim_user(user_id)
);

CREATE INDEX idx_expenses_user ON fact_expenses(user_id);