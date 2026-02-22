mod carbon;
mod config;
mod latency;
mod proxy;
mod router;
mod state;

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Router,
};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::net::TcpListener;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::{info, warn};

use crate::state::AppState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "carbon_router=info,tower_http=info".into()),
        )
        .init();

    // Load config
    let config_path = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("config.toml"));

    info!(path = %config_path.display(), "Loading configuration");
    let config = config::AppConfig::load(&config_path)?;
    let bind_addr = config.server.bind.clone();

    info!(
        backends = config.backends.len(),
        carbon_weight = config.weights.carbon,
        latency_weight = config.weights.latency,
        cost_weight = config.weights.cost,
        "Configuration loaded"
    );

    // Initialize shared state
    let state = AppState::new(config);

    // Log initial latency estimates
    {
        let backends = state.backends.read().await;
        for (id, b) in backends.iter() {
            info!(
                backend = %id,
                estimated_latency_ms = b.estimated_latency_ms,
                base_carbon = b.config.base_carbon,
                "Backend initialized"
            );
        }
    }

    // Spawn background tasks
    let carbon_state = state.clone();
    tokio::spawn(async move {
        carbon::carbon_poller(carbon_state).await;
    });

    let health_state = state.clone();
    tokio::spawn(async move {
        health_checker(health_state).await;
    });

    let latency_state = state.clone();
    tokio::spawn(async move {
        latency_prober(latency_state).await;
    });

    // Build router
    let app = Router::new()
        .route("/carbon-router/health", get(health_endpoint))
        .route("/carbon-router/status", get(status_endpoint))
        .route("/carbon-router/backends", get(backends_endpoint))
        .fallback(proxy::proxy_handler)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    info!(bind = %bind_addr, "Starting Carbon-Aware Inference Router");
    let listener = TcpListener::bind(&bind_addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

// === Diagnostic Endpoints ===

async fn health_endpoint() -> &'static str {
    "ok"
}

async fn status_endpoint(
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let snapshots = state.snapshot().await;
    let decision = router::select_backend(&snapshots, &state.config.weights);

    let status = serde_json::json!({
        "status": "running",
        "weights": {
            "carbon": state.config.weights.carbon,
            "latency": state.config.weights.latency,
            "cost": state.config.weights.cost,
        },
        "best_backend": decision.as_ref().map(|d| serde_json::json!({
            "id": d.backend_id,
            "score": d.score,
            "carbon": d.carbon,
            "latency_ms": d.latency_ms,
            "load_pct": d.load_pct,
        })),
        "candidates": decision.as_ref().map(|d| &d.candidates),
    });

    (StatusCode::OK, axum::Json(status))
}

async fn backends_endpoint(
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let backends = state.backends.read().await;
    let details: Vec<serde_json::Value> = backends
        .values()
        .map(|b| {
            serde_json::json!({
                "id": b.config.id,
                "name": b.config.name,
                "url": b.config.url,
                "lat": b.config.lat,
                "lng": b.config.lng,
                "power_type": format!("{:?}", b.config.power_type),
                "healthy": b.healthy,
                "consecutive_failures": b.consecutive_failures,
                "current_carbon": b.current_carbon,
                "carbon_source": b.carbon_source,
                "carbon_last_updated": b.carbon_last_updated,
                "estimated_latency_ms": b.estimated_latency_ms,
                "measured_latency_ms": b.measured_latency_ms,
                "active_requests": b.active_requests,
                "total_requests": b.total_requests,
            })
        })
        .collect();

    (StatusCode::OK, axum::Json(serde_json::json!({ "backends": details })))
}

// === Background Tasks ===

/// Health checker: GET /health on each backend every interval
async fn health_checker(state: Arc<AppState>) {
    let interval = std::time::Duration::from_secs(state.config.health.interval_secs);
    let timeout = std::time::Duration::from_secs(state.config.health.timeout_secs);
    let threshold = state.config.health.unhealthy_threshold;

    // Wait a brief moment before first check
    tokio::time::sleep(std::time::Duration::from_secs(5)).await;

    loop {
        let backend_urls: Vec<(String, String)> = {
            let backends = state.backends.read().await;
            backends
                .values()
                .map(|b| (b.config.id.clone(), b.config.url.clone()))
                .collect()
        };

        for (id, url) in &backend_urls {
            let health_url = format!("{}/health", url.trim_end_matches('/'));
            let client = state.client.clone();

            let result = tokio::time::timeout(
                timeout,
                client.get(&health_url).send(),
            )
            .await;

            let mut backends = state.backends.write().await;
            if let Some(b) = backends.get_mut(id) {
                match result {
                    Ok(Ok(resp)) if resp.status().is_success() => {
                        if !b.healthy {
                            info!(backend = %id, "Backend recovered");
                        }
                        b.healthy = true;
                        b.consecutive_failures = 0;
                    }
                    _ => {
                        b.consecutive_failures += 1;
                        if b.consecutive_failures >= threshold {
                            if b.healthy {
                                warn!(
                                    backend = %id,
                                    failures = b.consecutive_failures,
                                    "Backend marked unhealthy"
                                );
                            }
                            b.healthy = false;
                        }
                    }
                }
            }
        }

        tokio::time::sleep(interval).await;
    }
}

/// Latency prober: measures RTT to each backend, updates with EMA (alpha=0.3)
async fn latency_prober(state: Arc<AppState>) {
    let interval = std::time::Duration::from_secs(state.config.latency.probe_interval_secs);
    let probe_path = state.config.latency.probe_path.clone();

    // Wait before first probe
    tokio::time::sleep(std::time::Duration::from_secs(10)).await;

    loop {
        let backend_urls: Vec<(String, String)> = {
            let backends = state.backends.read().await;
            backends
                .values()
                .map(|b| (b.config.id.clone(), b.config.url.clone()))
                .collect()
        };

        for (id, url) in &backend_urls {
            let probe_url = format!(
                "{}{}",
                url.trim_end_matches('/'),
                if probe_path.starts_with('/') { &probe_path } else { "/" }
            );

            let start = std::time::Instant::now();
            let result = tokio::time::timeout(
                std::time::Duration::from_secs(5),
                state.client.get(&probe_url).send(),
            )
            .await;

            if let Ok(Ok(_)) = result {
                let rtt_ms = start.elapsed().as_secs_f64() * 1000.0;
                let mut backends = state.backends.write().await;
                if let Some(b) = backends.get_mut(id.as_str()) {
                    // EMA smoothing: new = alpha * measured + (1-alpha) * old
                    let alpha = 0.3;
                    let smoothed = match b.measured_latency_ms {
                        Some(old) => alpha * rtt_ms + (1.0 - alpha) * old,
                        None => rtt_ms,
                    };
                    b.measured_latency_ms = Some(smoothed);
                }
            }
        }

        tokio::time::sleep(interval).await;
    }
}
