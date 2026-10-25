# ⚡ Cloud Spot Arbitrage Wire

An autonomous compute broker running entirely on GitHub Actions. It monitors spot GPU rates across cloud providers, calculates normalized Price-per-TFLOPS metrics, and outputs clean machine feeds for agent workloads.

## Token Reduction Metrics
- **Average Token Savings**: ~95%
- **Input Overhead**: Replaces 25,000+ tokens of multi-provider documentation with a structured 3 KB JSON ranking.

## Machine Feeds
- [`v1/cheapest-compute.json`](public/v1/cheapest-compute.json)
- [`v1/provider-pricing.json`](public/v1/provider-pricing.json)
- [`llms.txt`](public/llms.txt)
- `pricing.parquet` (Columnar database snapshot)

## Architecture
- **Ingestion**: Live spot market APIs (Vast.ai, RunPod, Lambda, AWS, GCP)
- **Engine**: Python Quantitative Engine + DuckDB
- **Ledger**: Append-only Git-committed JSONL in `/data/history`
- **Edge CDN**: GitHub Pages (Fastly CDN)
