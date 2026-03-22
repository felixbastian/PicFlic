-- =========================
-- DIMENSION TABLES
-- =========================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER SCHEMA public OWNER TO app_user;

GRANT ALL ON SCHEMA public TO app_user;

CREATE TABLE dim_user (
    user_id UUID PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE,
    username TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT
);

CREATE TABLE dim_models (
    model_id UUID PRIMARY KEY,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    image_input_costs_in_$_per_1m_token NUMERIC(12,6),
    text_input_costs_in_$_per_1m_token NUMERIC(12,6),
    text_output_costs_in_$_per_1m_token NUMERIC(12,6)
);

-- =========================
-- FACT TABLES
-- =========================

CREATE TABLE fact_usage (
    usage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    model_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    nb_input_image_token INTEGER DEFAULT 0,
    nb_input_text_token INTEGER DEFAULT 0,
    nb_output_text_token INTEGER DEFAULT 0,

    CONSTRAINT fk_usage_user
        FOREIGN KEY (user_id) REFERENCES dim_user(user_id),

    CONSTRAINT fk_usage_model
        FOREIGN KEY (model_id) REFERENCES dim_models(model_id)
);

CREATE TABLE fact_consumption (
    meal_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    category TEXT,
    calories INTEGER,
    tags TEXT[],
    alcohol_units NUMERIC(6,2),

    CONSTRAINT fk_consumption_user
        FOREIGN KEY (user_id) REFERENCES dim_user(user_id)
);

CREATE TABLE fact_dishes (
    dish_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    picture_link TEXT,
    name TEXT NOT NULL,
    description TEXT,

    CONSTRAINT fk_dishes_user
        FOREIGN KEY (user_id) REFERENCES dim_user(user_id)
);

-- =========================
-- INDEXES (important)
-- =========================

-- Usage queries
CREATE INDEX idx_usage_user ON fact_usage(user_id);
CREATE INDEX idx_usage_model ON fact_usage(model_id);
CREATE INDEX idx_usage_created_at ON fact_usage(created_at);

-- Consumption queries
CREATE INDEX idx_consumption_user ON fact_consumption(user_id);

-- Dishes queries
CREATE INDEX idx_dishes_user ON fact_dishes(user_id);

-- Model lookup
CREATE INDEX idx_models_provider_name ON dim_models(model_provider, model_name);
