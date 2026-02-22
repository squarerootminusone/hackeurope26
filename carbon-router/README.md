# Carbon-Aware Inference Router

A Rust reverse proxy that routes OpenAI-compatible inference requests to the datacenter with the lowest carbon intensity, factoring in latency and load.

```
Client --> [carbon-router :8080] --score backends--> [best datacenter]
                |                                          |
                |<--------stream SSE bytes back------------|
```

## How It Works

Every incoming request is scored against all healthy backends using a weighted formula:

```
score = w_carbon * (carbon / 500) + w_latency * (latency / 300) + w_cost * (load / 100)
```

The request is forwarded to the lowest-scoring backend. SSE/streaming responses are relayed byte-for-byte with zero parsing overhead.

Three background tasks keep state fresh:

| Task | Default interval | What it does |
|------|-----------------|--------------|
| Carbon poller | 5 min | Fetches gCO2/kWh from multiple sources (ElectricityMaps, WattTime, UK Carbon Intensity) with automatic fallback |
| Health checker | 30 sec | `GET /health` on each backend, marks unhealthy after 3 consecutive failures |
| Latency prober | 60 sec | Measures RTT, smooths with EMA (alpha=0.3) |

## Quick Start

```bash
cargo build --release
./target/release/carbon-router config.toml
```

The router binds to `0.0.0.0:8080` by default. All requests not matching `/carbon-router/*` are proxied to the optimal backend.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/carbon-router/health` | Liveness check, returns `ok` |
| `GET` | `/carbon-router/status` | JSON: current best backend, all scored candidates, weights |
| `GET` | `/carbon-router/backends` | JSON: detailed per-backend state (carbon, latency, health, request counts) |
| `ANY` | `/*` | Reverse proxy to the optimal backend |

### Example: check routing status

```bash
curl -s http://localhost:8080/carbon-router/status | jq .
```

```json
{
  "status": "running",
  "weights": { "carbon": 0.7, "latency": 0.2, "cost": 0.1 },
  "best_backend": {
    "id": "eu-iceland1-a",
    "score": 0.037,
    "carbon": 12.0,
    "latency_ms": 42.0,
    "load_pct": 0.0
  },
  "candidates": [...]
}
```

### Example: proxy a streaming request

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"model":"llama-3","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

The response includes routing metadata headers:

```
x-carbon-router-backend: eu-iceland1-a
x-carbon-router-score: 0.037
x-carbon-intensity: 12.0
x-estimated-latency-ms: 42
```

## Configuration

See [`config.toml`](config.toml) for a full example. Key sections:

### Weights

```toml
[weights]
carbon = 0.7    # prioritize low carbon
latency = 0.2   # some latency sensitivity
cost = 0.1      # minor load balancing
# Must sum to 1.0
```

### Carbon Sources

The router supports three carbon intensity APIs. Configure one or more in `config.toml`:

```toml
[carbon]
poll_interval_secs = 300
use_simulated_fallback = true

[carbon.electricity_maps]
api_key = "your-key"    # Free tier: 50 req/hr, global coverage

[carbon.watttime]
username = "fourk0"       # Free tier: marginal emissions percentile, global
password = "pwd4k0sp!!!"

[carbon.uk_carbon_intensity]
enabled = true          # Free, unlimited, no auth — GB only
```

| Source | Coverage | Auth | Rate Limit | Data |
|--------|----------|------|------------|------|
| [ElectricityMaps](https://www.electricitymaps.com/) | Global (by zone) | API key | 50 req/hr (free) | Direct gCO2eq/kWh |
| [WattTime](https://watttime.org/) | Global (by lat/lng) | Username/password | Generous | Marginal emissions percentile (converted to estimated gCO2) |
| [UK Carbon Intensity](https://carbonintensity.org.uk/) | Great Britain | None | Unlimited | Actual/forecast gCO2/kWh |

**Auto-detection** (default `carbon_source = "auto"` on each backend):
1. UK Carbon Intensity — if backend is in GB (lat 49.9-60.9, lng -8.2 to 1.8) and enabled
2. WattTime — if credentials are configured
3. ElectricityMaps — if API key is configured
4. Simulated fallback — time-of-day sinusoidal model

You can override the source per-backend:
```toml
[[backend]]
id = "uk-london"
carbon_source = "uk_carbon_intensity"  # force specific source
```

When `use_simulated_fallback = true` (the default) and no API sources are configured, carbon intensity is modeled using a time-of-day sinusoidal curve. Renewable backends stay near their base carbon; grid backends fluctuate with a peak around 6 PM UTC. The simulated model also serves as the last-resort fallback when all configured APIs fail.

### Backends

```toml
[[backend]]
id = "eu-iceland1-a"
name = "ICE02 Keflavík"
url = "http://your-inference-endpoint:8000"
lat = 64.0
lng = -22.5
power_type = "renewable"   # renewable | hybrid | grid | ccs
base_carbon = 12.0
electricity_maps_zone = "IS"
gpus = ["GB200 NVL72", "A100", "H100"]
```

The included config has all 9 Crusoe Cloud regions with placeholder URLs. Replace `url` values with your actual inference endpoints.

## Latency Model

When no measured latency is available (backends unreachable for probing), the router uses a geographic estimation model:

- **11 global network hubs** (NYC/Ashburn, LA/SJC, Dallas, London, Frankfurt, Singapore, Tokyo, Sydney, Sao Paulo, Mumbai, Reykjavik)
- **20 known hub-to-hub RTTs** (e.g. NYC-London: 35ms, LA-Tokyo: 55ms)
- **Last-mile estimation** from haversine distance to nearest hub
- Falls back to fiber-distance calculation with submarine cable crossing penalties for unknown hub pairs

The model is deterministic (no jitter) for routing stability.

## Architecture

```
src/
  main.rs       Entry point, background task spawning, diagnostic endpoints
  config.rs     TOML config structs with validation
  state.rs      Arc<RwLock<>> shared state, snapshot pattern for lock-free scoring
  latency.rs    Haversine, network hubs, crossing penalties, latency estimation
  carbon.rs     Multi-source carbon API client (ElectricityMaps, WattTime, UK) + simulated fallback
  router.rs     Weighted scoring algorithm, backend selection
  proxy.rs      Request forwarding, SSE byte-stream relay, request tracking
```

Key design decisions:

- **Snapshot pattern** - scoring reads a lightweight copy of backend state, never holds the lock during computation or I/O
- **Raw byte relay** - SSE responses are streamed as raw bytes with zero parsing or buffering
- **DropGuard tracking** - active request count is decremented automatically when the response stream ends, even on client disconnect
- **Multi-source fallback** - carbon polling tries sources in priority order per-backend, falling back to simulated on failure
- **Rate-limit awareness** - 200ms delay between ElectricityMaps requests; WattTime and UK API have generous limits

## Testing

```bash
cargo test
```

Runs unit tests covering latency estimation, routing logic, and carbon source conversion (WattTime percentile, GB detection).

## Logging

Control log level with the `RUST_LOG` environment variable:

```bash
RUST_LOG=carbon_router=debug ./target/release/carbon-router config.toml
```
