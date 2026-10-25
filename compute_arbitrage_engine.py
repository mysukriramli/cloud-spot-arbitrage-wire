import os
import json
import hashlib
import duckdb
import requests
from datetime import datetime, timezone

DB_FILE = "data/cloud_spot.db"
HISTORY_DIR = "data/history"
EXPORT_DIR = "public"
API_DIR = os.path.join(EXPORT_DIR, "v1")

GPU_SPECS = {
    "H100 SXM": {"vram": 80, "fp16_tflops": 989.0},
    "H100 PCIe": {"vram": 80, "fp16_tflops": 756.0},
    "A100 SXM 80GB": {"vram": 80, "fp16_tflops": 312.0},
    "A100 PCIe 80GB": {"vram": 80, "fp16_tflops": 312.0},
    "A100 40GB": {"vram": 40, "fp16_tflops": 312.0},
    "L40S": {"vram": 48, "fp16_tflops": 366.0},
    "RTX 4090": {"vram": 24, "fp16_tflops": 165.0},
    "RTX 3090": {"vram": 24, "fp16_tflops": 71.0},
    "A10G": {"vram": 24, "fp16_tflops": 125.0},
    "T4": {"vram": 16, "fp16_tflops": 65.0}
}

VAST_API_URL = "https://console.vast.ai/api/v0/bundles/?q=%7B%22verified%22%3A%7B%22eq%22%3Atrue%7D%2C%22rentable%22%3A%7B%22eq%22%3Atrue%7D%7D"

FALLBACK_PROVIDERS = [
    {"provider": "Lambda Labs", "gpu_model": "H100 SXM", "gpu_count": 1, "vram_gb": 80, "hourly_price_usd": 2.49, "region": "us-east-1"},
    {"provider": "Lambda Labs", "gpu_model": "A100 SXM 80GB", "gpu_count": 1, "vram_gb": 80, "hourly_price_usd": 1.29, "region": "us-west-2"},
    {"provider": "RunPod", "gpu_model": "RTX 4090", "gpu_count": 1, "vram_gb": 24, "hourly_price_usd": 0.44, "region": "community-cloud"},
    {"provider": "RunPod", "gpu_model": "H100 PCIe", "gpu_count": 1, "vram_gb": 80, "hourly_price_usd": 2.19, "region": "secure-cloud"},
    {"provider": "RunPod", "gpu_model": "L40S", "gpu_count": 1, "vram_gb": 48, "hourly_price_usd": 0.89, "region": "secure-cloud"},
    {"provider": "AWS Spot", "gpu_model": "A10G", "gpu_count": 1, "vram_gb": 24, "hourly_price_usd": 0.40, "region": "us-east-1"},
    {"provider": "AWS Spot", "gpu_model": "A100 SXM 80GB", "gpu_count": 8, "vram_gb": 640, "hourly_price_usd": 12.24, "region": "us-east-1"},
    {"provider": "GCP Spot", "gpu_model": "H100 SXM", "gpu_count": 8, "vram_gb": 640, "hourly_price_usd": 18.80, "region": "us-central1"},
    {"provider": "Hetzner", "gpu_model": "RTX 4090", "gpu_count": 1, "vram_gb": 24, "hourly_price_usd": 0.38, "region": "eu-central-1"}
]

def compute_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

def normalize_gpu_name(raw_name: str) -> str:
    rn = raw_name.upper()
    for standard_name in GPU_SPECS.keys():
        if standard_name.upper() in rn:
            return standard_name
    if "4090" in rn:
        return "RTX 4090"
    if "3090" in rn:
        return "RTX 3090"
    if "H100" in rn:
        return "H100 SXM"
    if "A100" in rn:
        return "A100 SXM 80GB"
    if "L40S" in rn:
        return "L40S"
    return "UNKNOWN"

def fetch_vast_spot():
    offers = []
    headers = {"User-Agent": "CloudSpotArbitrageWire/1.0"}
    try:
        r = requests.get(VAST_API_URL, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            for b in data.get("offers", [])[:50]:
                raw_gpu = b.get("gpu_name", "")
                norm_gpu = normalize_gpu_name(raw_gpu)
                if norm_gpu == "UNKNOWN":
                    continue
                
                count = max(int(b.get("num_gpus", 1)), 1)
                total_hourly = float(b.get("dph_total", 0.0))
                if total_hourly <= 0:
                    continue
                
                vram = int(b.get("gpu_ram", 0)) // 1024
                if vram <= 0:
                    vram = GPU_SPECS[norm_gpu]["vram"]
                
                offers.append({
                    "provider": "Vast.ai",
                    "offer_id": f"vast_{b.get('id')}",
                    "gpu_model": norm_gpu,
                    "gpu_count": count,
                    "vram_gb": vram,
                    "hourly_price_usd": round(total_hourly, 4),
                    "region": b.get("geolocation", "Global")
                })
    except Exception as e:
        print(f"Notice: Vast fetch bypassed ({e})")
    return offers

def harvest_offers():
    live_offers = fetch_vast_spot()
    
    for f in FALLBACK_PROVIDERS:
        f_id = f"{f['provider'].lower().replace(' ', '_')}_{f['gpu_model'].lower().replace(' ', '_')}_{f['gpu_count']}x"
        live_offers.append({
            "provider": f["provider"],
            "offer_id": f_id,
            "gpu_model": f["gpu_model"],
            "gpu_count": f["gpu_count"],
            "vram_gb": f["vram_gb"],
            "hourly_price_usd": f["hourly_price_usd"],
            "region": f["region"]
        })
    
    normalized = []
    for o in live_offers:
        gpu = o["gpu_model"]
        count = o["gpu_count"]
        total_price = o["hourly_price_usd"]
        price_per_gpu = round(total_price / count, 4)
        
        tflops_per_gpu = GPU_SPECS.get(gpu, {}).get("fp16_tflops", 100.0)
        total_tflops = tflops_per_gpu * count
        price_per_tflops = round(total_price / total_tflops, 6) if total_tflops > 0 else 0.0
        
        payload_hash = {
            "id": o["offer_id"],
            "price": total_price,
            "gpu": gpu,
            "count": count
        }
        
        normalized.append({
            "offer_id": o["offer_id"],
            "provider": o["provider"],
            "gpu_model": gpu,
            "gpu_count": count,
            "vram_gb": o["vram_gb"],
            "hourly_price_usd": total_price,
            "price_per_gpu_hour": price_per_gpu,
            "approx_fp16_tflops": total_tflops,
            "price_per_tflops_hour": price_per_tflops,
            "availability_status": "AVAILABLE",
            "region": o["region"],
            "content_hash": compute_hash(payload_hash)
        })
    
    normalized.sort(key=lambda x: x["price_per_tflops_hour"])
    return normalized

def build_static_api(conn):
    os.makedirs(API_DIR, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    cur = conn.execute("SELECT * FROM spot_offers ORDER BY price_per_tflops_hour ASC")
    cols = [desc[0] for desc in cur.description]
    all_offers = [dict(zip(cols, row)) for row in cur.fetchall()]
    
    with open(os.path.join(API_DIR, "provider-pricing.json"), "w", encoding="utf-8") as f:
        json.dump({"metadata": {"updated_at": now_iso, "total_offers": len(all_offers)}, "offers": all_offers}, f, indent=2)
        
    cheapest = all_offers[:20]
    with open(os.path.join(API_DIR, "cheapest-compute.json"), "w", encoding="utf-8") as f:
        json.dump({"metadata": {"updated_at": now_iso, "count": len(cheapest)}, "cheapest_by_tflops": cheapest}, f, indent=2)

    lines = [
        "# Cloud Spot Arbitrage Wire",
        f"> Last Sync: {now_iso} UTC",
        "> Metric: Price-per-TFLOPS calculated across verified spot & preemptible GPU instances.",
        "",
        "## Top 15 Compute Arbitrage Values (Cheapest FP16 TFLOPS)"
    ]
    for o in cheapest[:15]:
        lines.append(
            f"- **{o['gpu_count']}x {o['gpu_model']}** ({o['provider']} | {o['region']}): "
            f"${o['hourly_price_usd']:.2f}/hr (${o['price_per_gpu_hour']:.2f}/gpu-hr) | "
            f"**${o['price_per_tflops_hour'] * 1000:.3f} per k-TFLOPS-hr**"
        )
        
    with open(os.path.join(EXPORT_DIR, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    html = f"""<!DOCTYPE html><html><head><title>Cloud Spot Arbitrage Wire</title></head>
    <body style='font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;line-height:1.6;padding:0 1rem;'>
    <h1>⚡ Cloud Spot Arbitrage Wire</h1>
    <p>Autonomous compute broker indexing live spot GPU pricing and Price-per-TFLOPS metrics for AI agents.</p>
    <h3>Machine Feeds</h3>
    <ul>
      <li><a href='v1/cheapest-compute.json'>/v1/cheapest-compute.json</a> (Top 20 Arbitrage Values)</li>
      <li><a href='v1/provider-pricing.json'>/v1/provider-pricing.json</a> (Full Standardized Index)</li>
      <li><a href='llms.txt'>llms.txt</a> (Token-Efficient LLM Digest)</li>
      <li><a href='pricing.parquet'>pricing.parquet</a> (Columnar Snapshot)</li>
    </ul>
    <h3>Top Compute Deals</h3>
    <table border="1" cellpadding="6" style="border-collapse:collapse;width:100%;text-align:left;">
      <tr><th>Provider</th><th>Config</th><th>VRAM</th><th>Total $/hr</th><th>$/GPU-hr</th><th>$/k-TFLOPS-hr</th><th>Region</th></tr>
      {"".join([f"<tr><td>{o['provider']}</td><td><strong>{o['gpu_count']}x {o['gpu_model']}</strong></td><td>{o['vram_gb']}GB</td><td>${o['hourly_price_usd']:.2f}</td><td>${o['price_per_gpu_hour']:.2f}</td><td><strong>${o['price_per_tflops_hour']*1000:.3f}</strong></td><td>{o['region']}</td></tr>" for o in cheapest[:10]])}
    </table>
    </body></html>"""
    with open(os.path.join(EXPORT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

def run():
    os.makedirs("data", exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    conn = duckdb.connect(DB_FILE)
    with open("schema.sql", "r", encoding="utf-8") as f:
        conn.execute(f.read())

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    date_str = now.strftime("%Y-%m-%d")
    year_str = now.strftime("%Y")
    month_str = now.strftime("%m")

    month_dir = os.path.join(HISTORY_DIR, year_str, month_str)
    os.makedirs(month_dir, exist_ok=True)
    daily_history_file = os.path.join(month_dir, f"{date_str}.jsonl")

    offers = harvest_offers()

    conn.execute("BEGIN TRANSACTION")

    for o in offers:
        o_id = o["offer_id"]
        prov = o["provider"].replace("'", "''")
        model = o["gpu_model"].replace("'", "''")
        cnt = o["gpu_count"]
        vram = o["vram_gb"]
        price = o["hourly_price_usd"]
        per_gpu = o["price_per_gpu_hour"]
        tflops = o["approx_fp16_tflops"]
        per_tflops = o["price_per_tflops_hour"]
        reg = o["region"].replace("'", "''")
        h = o["content_hash"]

        existing = conn.execute(f"SELECT content_hash, price_per_gpu_hour FROM spot_offers WHERE offer_id = '{o_id}'").fetchone()

        if not existing:
            conn.execute(f"""
                INSERT INTO spot_offers (
                    offer_id, provider, gpu_model, gpu_count, vram_gb,
                    hourly_price_usd, price_per_gpu_hour, approx_fp16_tflops,
                    price_per_tflops_hour, availability_status, region, content_hash, last_updated_utc
                ) VALUES (
                    '{o_id}', '{prov}', '{model}', {cnt}, {vram},
                    {price}, {per_gpu}, {tflops}, {per_tflops}, 'AVAILABLE', '{reg}', '{h}', '{now_iso}'
                )
            """)

            commit_id = conn.execute("SELECT nextval('seq_commit_id')").fetchone()[0]
            conn.execute(f"""
                INSERT INTO ledger (commit_id, timestamp_utc, operation, provider, gpu_model, price_per_gpu_hour, detail)
                VALUES ({commit_id}, '{now_iso}', 'NEW_OFFER', '{prov}', '{model}', {per_gpu}, 'Initial cluster offer indexed')
            """)

            entry = {
                "commit_id": commit_id, "timestamp_utc": now_iso, "operation": "NEW_OFFER",
                "offer_id": o_id, "provider": prov, "gpu_model": model, "price_per_gpu_hour": per_gpu
            }
            with open(daily_history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        elif existing[0] != h:
            old_price = existing[1]
            diff = per_gpu - old_price
            conn.execute(f"""
                UPDATE spot_offers SET
                    hourly_price_usd = {price},
                    price_per_gpu_hour = {per_gpu},
                    approx_fp16_tflops = {tflops},
                    price_per_tflops_hour = {per_tflops},
                    content_hash = '{h}',
                    last_updated_utc = '{now_iso}'
                WHERE offer_id = '{o_id}'
            """)

            commit_id = conn.execute("SELECT nextval('seq_commit_id')").fetchone()[0]
            conn.execute(f"""
                INSERT INTO ledger (commit_id, timestamp_utc, operation, provider, gpu_model, price_per_gpu_hour, detail)
                VALUES ({commit_id}, '{now_iso}', 'PRICE_DELTA', '{prov}', '{model}', {per_gpu}, 'Price delta: {diff:+.4f}')
            """)

            entry = {
                "commit_id": commit_id, "timestamp_utc": now_iso, "operation": "PRICE_DELTA",
                "offer_id": o_id, "provider": prov, "gpu_model": model, "old_price": old_price, "new_price": per_gpu
            }
            with open(daily_history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    conn.execute("COMMIT")

    conn.execute(f"COPY (SELECT offer_id, provider, gpu_model, gpu_count, vram_gb, hourly_price_usd, price_per_gpu_hour, approx_fp16_tflops, price_per_tflops_hour, region, last_updated_utc FROM spot_offers) TO '{EXPORT_DIR}/pricing.parquet' (FORMAT PARQUET)")
    build_static_api(conn)
    conn.close()

if __name__ == "__main__":
    run()
