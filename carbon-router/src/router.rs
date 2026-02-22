use crate::config::WeightsConfig;
use crate::state::BackendSnapshot;

/// Result of the routing decision
#[derive(Debug, Clone, serde::Serialize)]
pub struct RouteDecision {
    pub backend_id: String,
    pub backend_url: String,
    pub score: f64,
    pub carbon: f64,
    pub latency_ms: f64,
    pub load_pct: f64,
    pub candidates: Vec<ScoredCandidate>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ScoredCandidate {
    pub id: String,
    pub name: String,
    pub score: f64,
    pub carbon: f64,
    pub latency_ms: f64,
    pub load_pct: f64,
    pub healthy: bool,
}

/// Weighted scoring algorithm ported from JSX lines 703-712:
///   score = w_carbon * (carbon/500) + w_latency * (latency/300) + w_cost * (load/100)
/// Filters to healthy backends, picks lowest score.
pub fn select_backend(
    snapshots: &[BackendSnapshot],
    weights: &WeightsConfig,
) -> Option<RouteDecision> {
    let mut candidates: Vec<ScoredCandidate> = snapshots
        .iter()
        .map(|s| {
            let carbon_norm = s.carbon / 500.0;
            let latency_norm = s.latency_ms / 300.0;
            let cost_norm = s.load_pct / 100.0;
            let score = weights.carbon * carbon_norm
                + weights.latency * latency_norm
                + weights.cost * cost_norm;
            ScoredCandidate {
                id: s.id.clone(),
                name: s.name.clone(),
                score: (score * 1000.0).round() / 1000.0,
                carbon: s.carbon,
                latency_ms: s.latency_ms,
                load_pct: s.load_pct,
                healthy: s.healthy,
            }
        })
        .collect();

    // Sort all candidates by score for the response
    candidates.sort_by(|a, b| a.score.partial_cmp(&b.score).unwrap_or(std::cmp::Ordering::Equal));

    // Pick the lowest-scoring healthy backend
    let best = candidates.iter().find(|c| c.healthy)?;
    let best_snapshot = snapshots.iter().find(|s| s.id == best.id)?;

    Some(RouteDecision {
        backend_id: best.id.clone(),
        backend_url: best_snapshot.url.clone(),
        score: best.score,
        carbon: best.carbon,
        latency_ms: best.latency_ms,
        load_pct: best.load_pct,
        candidates,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_snapshot(id: &str, carbon: f64, latency: f64, load: f64, healthy: bool) -> BackendSnapshot {
        BackendSnapshot {
            id: id.to_string(),
            name: format!("Backend {}", id),
            healthy,
            carbon,
            latency_ms: latency,
            load_pct: load,
            active_requests: 0,
            total_requests: 0,
            url: format!("http://{}.example.com", id),
        }
    }

    #[test]
    fn test_selects_lowest_score() {
        let weights = WeightsConfig { carbon: 0.7, latency: 0.2, cost: 0.1 };
        let snapshots = vec![
            make_snapshot("high-carbon", 400.0, 20.0, 30.0, true),
            make_snapshot("low-carbon", 12.0, 40.0, 50.0, true),
        ];

        let decision = select_backend(&snapshots, &weights).unwrap();
        assert_eq!(decision.backend_id, "low-carbon");
    }

    #[test]
    fn test_skips_unhealthy() {
        let weights = WeightsConfig { carbon: 0.7, latency: 0.2, cost: 0.1 };
        let snapshots = vec![
            make_snapshot("best-but-down", 10.0, 10.0, 10.0, false),
            make_snapshot("second-best", 50.0, 20.0, 30.0, true),
        ];

        let decision = select_backend(&snapshots, &weights).unwrap();
        assert_eq!(decision.backend_id, "second-best");
    }

    #[test]
    fn test_all_unhealthy_returns_none() {
        let weights = WeightsConfig { carbon: 0.7, latency: 0.2, cost: 0.1 };
        let snapshots = vec![
            make_snapshot("a", 10.0, 10.0, 10.0, false),
            make_snapshot("b", 50.0, 20.0, 30.0, false),
        ];

        assert!(select_backend(&snapshots, &weights).is_none());
    }

    #[test]
    fn test_latency_weighted_routing() {
        let weights = WeightsConfig { carbon: 0.1, latency: 0.8, cost: 0.1 };
        let snapshots = vec![
            make_snapshot("low-carbon-far", 10.0, 200.0, 30.0, true),
            make_snapshot("high-carbon-near", 300.0, 15.0, 30.0, true),
        ];

        let decision = select_backend(&snapshots, &weights).unwrap();
        assert_eq!(decision.backend_id, "high-carbon-near");
    }

    #[test]
    fn test_score_formula() {
        let weights = WeightsConfig { carbon: 0.7, latency: 0.2, cost: 0.1 };
        let snapshots = vec![make_snapshot("test", 100.0, 60.0, 50.0, true)];

        let decision = select_backend(&snapshots, &weights).unwrap();
        // score = 0.7 * (100/500) + 0.2 * (60/300) + 0.1 * (50/100)
        //       = 0.7 * 0.2     + 0.2 * 0.2      + 0.1 * 0.5
        //       = 0.14          + 0.04            + 0.05
        //       = 0.23
        assert!((decision.score - 0.23).abs() < 0.001, "Score should be 0.23, got {}", decision.score);
    }

    #[test]
    fn test_candidates_sorted() {
        let weights = WeightsConfig { carbon: 0.7, latency: 0.2, cost: 0.1 };
        let snapshots = vec![
            make_snapshot("c", 400.0, 100.0, 80.0, true),
            make_snapshot("a", 10.0, 20.0, 10.0, true),
            make_snapshot("b", 150.0, 50.0, 40.0, true),
        ];

        let decision = select_backend(&snapshots, &weights).unwrap();
        let scores: Vec<f64> = decision.candidates.iter().map(|c| c.score).collect();
        for i in 1..scores.len() {
            assert!(scores[i] >= scores[i - 1], "Candidates should be sorted by score");
        }
    }
}
