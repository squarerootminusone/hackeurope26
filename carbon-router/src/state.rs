use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::config::{AppConfig, BackendConfig};
use crate::latency::{estimate_latency_ms, Point};

/// Per-backend mutable state
#[derive(Debug, Clone)]
pub struct BackendState {
    pub config: BackendConfig,
    pub healthy: bool,
    pub consecutive_failures: u32,
    pub current_carbon: f64,
    pub carbon_last_updated: Option<chrono::DateTime<chrono::Utc>>,
    pub carbon_source: String,
    pub measured_latency_ms: Option<f64>,
    pub estimated_latency_ms: f64,
    pub active_requests: u64,
    pub total_requests: u64,
}

/// Lightweight snapshot for scoring (avoids holding locks)
#[derive(Debug, Clone)]
pub struct BackendSnapshot {
    pub id: String,
    pub name: String,
    pub healthy: bool,
    pub carbon: f64,
    pub latency_ms: f64,
    pub load_pct: f64,
    pub active_requests: u64,
    pub total_requests: u64,
    pub url: String,
}

/// Shared application state
pub struct AppState {
    pub backends: RwLock<HashMap<String, BackendState>>,
    pub config: AppConfig,
    pub client: reqwest::Client,
    /// Origin point for latency estimation (default: Ashburn, VA)
    pub origin: Point,
}

impl AppState {
    pub fn new(config: AppConfig) -> Arc<Self> {
        let origin = Point { lat: 39.0, lng: -77.5 };
        let client = reqwest::Client::builder()
            .pool_max_idle_per_host(10)
            .timeout(std::time::Duration::from_secs(config.server.timeout_secs))
            .build()
            .expect("Failed to build reqwest client");

        let mut backends = HashMap::new();
        for b in &config.backends {
            let estimated = estimate_latency_ms(
                &origin,
                &Point { lat: b.lat, lng: b.lng },
            );
            backends.insert(
                b.id.clone(),
                BackendState {
                    config: b.clone(),
                    healthy: true, // Assume healthy until proven otherwise
                    consecutive_failures: 0,
                    current_carbon: b.base_carbon,
                    carbon_last_updated: None,
                    carbon_source: "pending".to_string(),
                    measured_latency_ms: None,
                    estimated_latency_ms: estimated,
                    active_requests: 0,
                    total_requests: 0,
                },
            );
        }

        Arc::new(Self {
            backends: RwLock::new(backends),
            config,
            client,
            origin,
        })
    }

    /// Take a consistent snapshot of all backends for scoring.
    /// Grabs the read lock briefly and copies lightweight data.
    pub async fn snapshot(&self) -> Vec<BackendSnapshot> {
        let backends = self.backends.read().await;
        backends
            .values()
            .map(|b| {
                // Use measured latency if available, otherwise estimated
                let latency = b.measured_latency_ms.unwrap_or(b.estimated_latency_ms);
                // Load as percentage: map active_requests to 0-100 range
                // Assume max ~10 concurrent requests = 100% load
                let load_pct = (b.active_requests as f64 / 10.0 * 100.0).min(100.0);
                BackendSnapshot {
                    id: b.config.id.clone(),
                    name: b.config.name.clone(),
                    healthy: b.healthy,
                    carbon: b.current_carbon,
                    latency_ms: latency,
                    load_pct,
                    active_requests: b.active_requests,
                    total_requests: b.total_requests,
                    url: b.config.url.clone(),
                }
            })
            .collect()
    }
}
