CREATE SEQUENCE IF NOT EXISTS seq_commit_id START 1;

CREATE TABLE IF NOT EXISTS spot_offers (
    offer_id VARCHAR PRIMARY KEY,
    provider VARCHAR,
    gpu_model VARCHAR,
    gpu_count INTEGER,
    vram_gb INTEGER,
    hourly_price_usd DOUBLE,
    price_per_gpu_hour DOUBLE,
    approx_fp16_tflops DOUBLE,
    price_per_tflops_hour DOUBLE,
    availability_status VARCHAR,
    region VARCHAR,
    content_hash VARCHAR,
    last_updated_utc VARCHAR
);

CREATE TABLE IF NOT EXISTS ledger (
    commit_id BIGINT PRIMARY KEY,
    timestamp_utc VARCHAR,
    operation VARCHAR,
    provider VARCHAR,
    gpu_model VARCHAR,
    price_per_gpu_hour DOUBLE,
    detail VARCHAR
);
