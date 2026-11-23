PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Справочники: последний известный снимок настроек Amazon Ads.
CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id     TEXT PRIMARY KEY,
    name            TEXT,
    state           TEXT,
    targeting_type  TEXT,
    budget          REAL,
    budget_type     TEXT,
    bid_strategy    TEXT,
    adj_top         REAL,
    adj_product_page REAL,
    adj_rest        REAL,
    start_date      TEXT,
    end_date        TEXT,
    serving_status  TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS dim_adgroup (
    ad_group_id     TEXT PRIMARY KEY,
    campaign_id     TEXT,
    name            TEXT,
    state           TEXT,
    default_bid     REAL,
    serving_status  TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS dim_target (
    target_id       TEXT PRIMARY KEY,
    campaign_id     TEXT,
    ad_group_id     TEXT,
    kind            TEXT,            -- KEYWORD | PRODUCT | AUTO
    targeting       TEXT,
    match_type      TEXT,
    is_negative     INTEGER DEFAULT 0,
    negative_level  TEXT,            -- CAMPAIGN | AD_GROUP | NULL
    state           TEXT,
    bid             REAL,
    bid_source      TEXT,            -- OWN | AD_GROUP_DEFAULT | NONE
    effective_bid   REAL,
    serving_status  TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS dim_ad (
    ad_id           TEXT PRIMARY KEY,
    campaign_id     TEXT,
    ad_group_id     TEXT,
    asin            TEXT,
    sku             TEXT,
    state           TEXT,
    serving_status  TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);

-- Дневные факты. Зерно — первичный ключ.
CREATE TABLE IF NOT EXISTS fact_campaign_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT, currency TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id)
);

CREATE TABLE IF NOT EXISTS fact_adgroup_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, ad_group_id TEXT NOT NULL, currency TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id)
);

CREATE TABLE IF NOT EXISTS fact_target_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, ad_group_id TEXT NOT NULL, target_id TEXT NOT NULL,
    targeting TEXT, match_type TEXT, currency TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, target_id)
);

CREATE TABLE IF NOT EXISTS fact_searchterm_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, ad_group_id TEXT NOT NULL,
    target_id TEXT NOT NULL, search_term TEXT NOT NULL,
    targeting TEXT, match_type TEXT, currency TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, target_id, search_term)
);

CREATE TABLE IF NOT EXISTS fact_placement_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, placement TEXT NOT NULL, placement_raw TEXT,
    currency TEXT, impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id, placement)
);

CREATE TABLE IF NOT EXISTS fact_product_day (
    date TEXT NOT NULL, campaign_id TEXT NOT NULL, ad_group_id TEXT NOT NULL, ad_id TEXT NOT NULL,
    asin TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
    source TEXT, loaded_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, ad_id, asin, marketplace)
);

-- Журнал изменений: и история Amazon, и собственный дифф снимков настроек.
CREATE TABLE IF NOT EXISTS changes (
    change_id     TEXT PRIMARY KEY,
    change_date   TEXT NOT NULL,
    change_ts     TEXT,
    date_precision TEXT DEFAULT 'DAY',
    entity_type   TEXT,
    entity_id     TEXT,
    campaign_id   TEXT,
    ad_group_id   TEXT,
    entity_label  TEXT,
    field         TEXT,
    old_value     TEXT,
    new_value     TEXT,
    delta         TEXT,
    source        TEXT,            -- AMAZON_HISTORY | SNAPSHOT_DIFF | AGENT_WRITE | IMPORT
    actor         TEXT,
    summary       TEXT,
    first_seen    TEXT
);

-- Служебное: запуски загрузки и охват по датасетам.
CREATE TABLE IF NOT EXISTS sync_runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,
    source      TEXT,
    files       INTEGER,
    rows        INTEGER,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS coverage (
    dataset     TEXT NOT NULL,
    date        TEXT NOT NULL,
    rows        INTEGER,
    spend       REAL,
    status      TEXT,
    run_id      TEXT,
    PRIMARY KEY (dataset, date)
);

CREATE INDEX IF NOT EXISTS idx_fc_date ON fact_campaign_day(date);
CREATE INDEX IF NOT EXISTS idx_ft_date ON fact_target_day(date);
CREATE INDEX IF NOT EXISTS idx_fs_date ON fact_searchterm_day(date);
CREATE INDEX IF NOT EXISTS idx_fp_date ON fact_placement_day(date);
CREATE INDEX IF NOT EXISTS idx_ch_date ON changes(change_date);
CREATE INDEX IF NOT EXISTS idx_ch_camp ON changes(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dt_camp ON dim_target(campaign_id, is_negative);
