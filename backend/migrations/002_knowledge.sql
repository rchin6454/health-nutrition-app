-- 002_knowledge: nutrition knowledge tables (architecture §10.2, implementation plan Phase 3).
-- Filled by the offline scripts in scripts/ingest/; every row carries the dataset_version of the
-- run that wrote it. Additions to §10.2: `diet` and `recommendable` on foods (vegetarian-first
-- recommendation lists), `origin` on synonyms (resolution preference), `note` on portions (the
-- assumption shown when a household measure is converted).

CREATE TABLE IF NOT EXISTS foods (
    id              TEXT PRIMARY KEY,        -- "ifct:A012", "usda:170567"
    name            TEXT NOT NULL,
    food_group      TEXT,
    state           TEXT CHECK (state IN ('raw', 'cooked')),
    dataset         TEXT NOT NULL CHECK (dataset IN ('IFCT2017', 'USDA_FDC')),
    diet            TEXT NOT NULL CHECK (diet IN ('veg', 'egg', 'nonveg')),
    recommendable   BOOLEAN NOT NULL,        -- false for spices, oils, sugars and USDA fallbacks
    dataset_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_synonyms (
    synonym         TEXT NOT NULL,           -- normalized: "dahi", "chawal", "baingan"
    food_id         TEXT NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    origin          TEXT NOT NULL CHECK (origin IN ('curated', 'dataset_name', 'local_name')),
    dataset_version TEXT NOT NULL,
    PRIMARY KEY (synonym, food_id)
);

CREATE TABLE IF NOT EXISTS nutrients (
    food_id         TEXT NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    nutrient        TEXT NOT NULL,           -- protein | iron | energy_kcal ...
    amount          REAL NOT NULL CHECK (amount >= 0),  -- per 100 g edible portion
    unit            TEXT NOT NULL,           -- g | mg | µg | kcal
    PRIMARY KEY (food_id, nutrient)
);
CREATE INDEX IF NOT EXISTS nutrients_nutrient_amount_idx ON nutrients (nutrient, amount DESC);

CREATE TABLE IF NOT EXISTS portion_weights (
    id              BIGSERIAL PRIMARY KEY,
    food_id         TEXT REFERENCES foods(id) ON DELETE CASCADE,  -- NULL = generic measure
    measure         TEXT NOT NULL,           -- one unit of: katori | cup | glass | tbsp | piece ...
    grams           REAL NOT NULL CHECK (grams > 0),
    note            TEXT,                    -- e.g. "1 medium roti (about 40 g)"
    dataset_version TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS portion_weights_food_measure_idx
    ON portion_weights (COALESCE(food_id, ''), measure);
