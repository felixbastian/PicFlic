CREATE TYPE carb_source AS ENUM (
    'noodles',
    'rice',
    'potato',
    'bread'
);

CREATE TYPE meat_type AS ENUM (
    'chicken',
    'beef',
    'porc',
    'fish'
);

CREATE TYPE frequency_rotation AS ENUM (
    'bi-weekly',
    'monthly',
    'occasionally',
    'seasonally'
);

ALTER TABLE fact_dishes
ADD COLUMN carb_source carb_source,
ADD COLUMN vegetarian BOOLEAN,
ADD COLUMN meat meat_type,
ADD COLUMN frequency_rotation frequency_rotation DEFAULT NULL;
