use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, HeaderValue, Request, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use futures_util::TryStreamExt;
use std::sync::Arc;
use tracing::{error, info, warn};

use crate::router::select_backend;
use crate::state::AppState;

/// Headers to strip when proxying (hop-by-hop headers)
const HOP_BY_HOP: &[&str] = &[
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
];

/// Guard that decrements active_requests when dropped (on stream completion)
struct ActiveRequestGuard {
    state: Arc<AppState>,
    backend_id: String,
}

impl Drop for ActiveRequestGuard {
    fn drop(&mut self) {
        let state = self.state.clone();
        let id = self.backend_id.clone();
        tokio::spawn(async move {
            let mut backends = state.backends.write().await;
            if let Some(b) = backends.get_mut(&id) {
                b.active_requests = b.active_requests.saturating_sub(1);
            }
        });
    }
}

/// Catch-all proxy handler: scores backends, forwards request, streams response.
pub async fn proxy_handler(
    State(state): State<Arc<AppState>>,
    req: Request<Body>,
) -> Response {
    // 1. Snapshot and route
    let snapshots = state.snapshot().await;
    let decision = match select_backend(&snapshots, &state.config.weights) {
        Some(d) => d,
        None => {
            warn!("No healthy backends available");
            return (
                StatusCode::BAD_GATEWAY,
                "No healthy backends available",
            )
                .into_response();
        }
    };

    let backend_id = decision.backend_id.clone();
    let backend_url = decision.backend_url.clone();

    info!(
        backend = %backend_id,
        score = %decision.score,
        carbon = %decision.carbon,
        latency = %decision.latency_ms,
        "Routing request"
    );

    // 2. Increment active requests
    {
        let mut backends = state.backends.write().await;
        if let Some(b) = backends.get_mut(&backend_id) {
            b.active_requests += 1;
            b.total_requests += 1;
        }
    }

    // 3. Build upstream URL
    let (parts, body) = req.into_parts();
    let upstream_uri = build_upstream_uri(&backend_url, &parts.uri);

    // 4. Build upstream request
    let mut upstream_req = state.client.request(parts.method.clone(), &upstream_uri);

    // Copy headers, stripping hop-by-hop
    for (name, value) in &parts.headers {
        let name_lower = name.as_str().to_lowercase();
        if !HOP_BY_HOP.contains(&name_lower.as_str()) {
            upstream_req = upstream_req.header(name.clone(), value.clone());
        }
    }

    // Attach body
    let body_bytes = match axum::body::to_bytes(body, state.config.server.max_body_bytes).await {
        Ok(b) => b,
        Err(e) => {
            error!(error = %e, "Failed to read request body");
            // Decrement active requests
            let mut backends = state.backends.write().await;
            if let Some(b) = backends.get_mut(&backend_id) {
                b.active_requests = b.active_requests.saturating_sub(1);
            }
            return (StatusCode::BAD_REQUEST, "Request body too large").into_response();
        }
    };

    if !body_bytes.is_empty() {
        upstream_req = upstream_req.body(body_bytes);
    }

    // 5. Send upstream request
    let upstream_resp = match upstream_req.send().await {
        Ok(r) => r,
        Err(e) => {
            error!(backend = %backend_id, error = %e, "Upstream request failed");
            // Mark failure
            let mut backends = state.backends.write().await;
            if let Some(b) = backends.get_mut(&backend_id) {
                b.active_requests = b.active_requests.saturating_sub(1);
                b.consecutive_failures += 1;
                if b.consecutive_failures >= state.config.health.unhealthy_threshold {
                    b.healthy = false;
                    warn!(backend = %backend_id, "Marking backend as unhealthy after {} consecutive failures", b.consecutive_failures);
                }
            }
            return (StatusCode::BAD_GATEWAY, format!("Upstream error: {}", e)).into_response();
        }
    };

    // 6. Build response with router headers
    let status = upstream_resp.status();
    let upstream_headers = upstream_resp.headers().clone();

    let mut response_headers = HeaderMap::new();
    for (name, value) in &upstream_headers {
        let name_lower = name.as_str().to_lowercase();
        if !HOP_BY_HOP.contains(&name_lower.as_str()) {
            response_headers.insert(name.clone(), value.clone());
        }
    }

    // Add carbon router metadata headers
    response_headers.insert(
        "x-carbon-router-backend",
        HeaderValue::from_str(&backend_id).unwrap_or_else(|_| HeaderValue::from_static("unknown")),
    );
    response_headers.insert(
        "x-carbon-router-score",
        HeaderValue::from_str(&format!("{:.3}", decision.score))
            .unwrap_or_else(|_| HeaderValue::from_static("0")),
    );
    response_headers.insert(
        "x-carbon-intensity",
        HeaderValue::from_str(&format!("{:.1}", decision.carbon))
            .unwrap_or_else(|_| HeaderValue::from_static("0")),
    );
    response_headers.insert(
        "x-estimated-latency-ms",
        HeaderValue::from_str(&format!("{:.0}", decision.latency_ms))
            .unwrap_or_else(|_| HeaderValue::from_static("0")),
    );

    // 7. Stream the response body back (zero-copy byte relay for SSE)
    let guard = ActiveRequestGuard {
        state: state.clone(),
        backend_id,
    };

    let byte_stream = upstream_resp.bytes_stream().map_err(|e| {
        let io_err = std::io::Error::new(std::io::ErrorKind::Other, e.to_string());
        io_err
    });

    // Keep the guard alive until the stream is done
    let body = Body::from_stream(GuardedStream {
        inner: Box::pin(byte_stream),
        _guard: guard,
    });

    let mut response = Response::new(body);
    *response.status_mut() = status;
    *response.headers_mut() = response_headers;

    response
}

/// A stream wrapper that holds the ActiveRequestGuard until the stream is exhausted.
use std::pin::Pin;
use std::task::{Context, Poll};
use futures_util::Stream;

struct GuardedStream {
    inner: Pin<Box<dyn Stream<Item = Result<bytes::Bytes, std::io::Error>> + Send>>,
    _guard: ActiveRequestGuard,
}

impl Stream for GuardedStream {
    type Item = Result<bytes::Bytes, std::io::Error>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

fn build_upstream_uri(backend_url: &str, original_uri: &Uri) -> String {
    let base = backend_url.trim_end_matches('/');
    let path_and_query = original_uri
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or("/");
    format!("{}{}", base, path_and_query)
}
