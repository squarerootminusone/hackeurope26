/// Latency estimation model ported from carbon-aware-router.jsx lines 82-187.
/// Deterministic (no jitter) for routing stability.

use std::f64::consts::PI;

#[derive(Debug, Clone)]
pub struct Point {
    pub lat: f64,
    pub lng: f64,
}

#[derive(Debug, Clone)]
struct NetworkHub {
    name: &'static str,
    lat: f64,
    lng: f64,
}

/// 11 global network hubs (JSX lines 92-104)
static NETWORK_HUBS: &[NetworkHub] = &[
    NetworkHub { name: "NYC/Ashburn", lat: 39.0, lng: -77.5 },
    NetworkHub { name: "LA/SJC", lat: 34.5, lng: -118.5 },
    NetworkHub { name: "Dallas", lat: 32.8, lng: -96.8 },
    NetworkHub { name: "London", lat: 51.5, lng: -0.1 },
    NetworkHub { name: "Frankfurt", lat: 50.1, lng: 8.7 },
    NetworkHub { name: "Singapore", lat: 1.3, lng: 103.8 },
    NetworkHub { name: "Tokyo", lat: 35.7, lng: 139.7 },
    NetworkHub { name: "Sydney", lat: -33.9, lng: 151.2 },
    NetworkHub { name: "São Paulo", lat: -23.5, lng: -46.6 },
    NetworkHub { name: "Mumbai", lat: 19.1, lng: 72.9 },
    NetworkHub { name: "Reykjavík", lat: 64.1, lng: -21.9 },
];

/// Known hub-to-hub RTTs in ms (JSX lines 106-127)
static CROSSING_PENALTIES: &[(&str, &str, f64)] = &[
    ("NYC/Ashburn", "London", 35.0),
    ("NYC/Ashburn", "Reykjavík", 28.0),
    ("London", "Reykjavík", 20.0),
    ("LA/SJC", "Tokyo", 55.0),
    ("LA/SJC", "Singapore", 85.0),
    ("LA/SJC", "Sydney", 75.0),
    ("NYC/Ashburn", "LA/SJC", 32.0),
    ("NYC/Ashburn", "Dallas", 18.0),
    ("Dallas", "LA/SJC", 16.0),
    ("London", "Frankfurt", 8.0),
    ("Tokyo", "Singapore", 38.0),
    ("Singapore", "Mumbai", 35.0),
    ("Singapore", "Sydney", 48.0),
    ("Tokyo", "Sydney", 58.0),
    ("London", "Mumbai", 55.0),
    ("London", "Singapore", 85.0),
    ("Frankfurt", "Mumbai", 50.0),
    ("London", "São Paulo", 95.0),
    ("NYC/Ashburn", "São Paulo", 65.0),
    ("Mumbai", "Sydney", 65.0),
];

/// Haversine distance in km (JSX lines 82-90)
pub fn haversine_km(p1: &Point, p2: &Point) -> f64 {
    let r = 6371.0;
    let to_rad = |d: f64| d * PI / 180.0;
    let d_lat = to_rad(p2.lat - p1.lat);
    let d_lng = to_rad(p2.lng - p1.lng);
    let a = (d_lat / 2.0).sin().powi(2)
        + to_rad(p1.lat).cos() * to_rad(p2.lat).cos() * (d_lng / 2.0).sin().powi(2);
    r * 2.0 * a.sqrt().atan2((1.0 - a).sqrt())
}

fn get_crossing_penalty(hub1: &str, hub2: &str) -> Option<f64> {
    for &(h1, h2, ms) in CROSSING_PENALTIES {
        if (h1 == hub1 && h2 == hub2) || (h2 == hub1 && h1 == hub2) {
            return Some(ms);
        }
    }
    None
}

struct NearestHubResult<'a> {
    hub: &'a NetworkHub,
    dist: f64,
}

fn nearest_hub(lat: f64, lng: f64) -> NearestHubResult<'static> {
    let p = Point { lat, lng };
    let mut best: Option<NearestHubResult> = None;
    for hub in NETWORK_HUBS {
        let d = haversine_km(&p, &Point { lat: hub.lat, lng: hub.lng });
        if best.is_none() || d < best.as_ref().unwrap().dist {
            best = Some(NearestHubResult { hub, dist: d });
        }
    }
    best.unwrap()
}

/// Estimate one-way latency in ms between two geographic points.
/// Deterministic port of JSX `simulateLatency` (lines 155-187) without jitter.
pub fn estimate_latency_ms(origin: &Point, dest: &Point) -> f64 {
    let direct_dist = haversine_km(origin, dest);

    // Very close: simple distance-based estimate
    if direct_dist < 200.0 {
        return (5.0 + direct_dist * 0.01).round().max(4.0);
    }

    let src_hub = nearest_hub(origin.lat, origin.lng);
    let dst_hub = nearest_hub(dest.lat, dest.lng);

    let src_last_mile = src_hub.dist * 0.008 + 3.0;
    let dst_last_mile = dst_hub.dist * 0.008 + 3.0;

    let hub_to_hub = if src_hub.hub.name == dst_hub.hub.name {
        2.0
    } else if let Some(known) = get_crossing_penalty(src_hub.hub.name, dst_hub.hub.name) {
        known
    } else {
        // Estimate from fiber distance
        let src_lng = src_hub.hub.lng;
        let dst_lng = dst_hub.hub.lng;
        let crosses_atlantic =
            (src_lng < -30.0 && dst_lng > -20.0) || (src_lng > -20.0 && dst_lng < -30.0);
        let crosses_pacific =
            (src_lng < -100.0 && dst_lng > 100.0) || (src_lng > 100.0 && dst_lng < -100.0);
        let hub_dist = haversine_km(
            &Point { lat: src_hub.hub.lat, lng: src_hub.hub.lng },
            &Point { lat: dst_hub.hub.lat, lng: dst_hub.hub.lng },
        );
        let fiber_ratio = if crosses_atlantic || crosses_pacific { 1.4 } else { 1.25 };
        let fiber_dist = hub_dist * fiber_ratio;
        let mut h2h = fiber_dist / 200.0;
        if crosses_atlantic {
            h2h += 8.0;
        }
        if crosses_pacific {
            h2h += 12.0;
        }
        h2h
    };

    let hops: f64 = if src_hub.hub.name == dst_hub.hub.name { 0.0 } else { 1.0 };
    let peering_overhead = hops * 3.0;
    let total = src_last_mile + hub_to_hub + dst_last_mile + peering_overhead;

    total.round().max(4.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_haversine_known_distance() {
        // NYC to London ~ 5570 km
        let nyc = Point { lat: 40.71, lng: -74.01 };
        let london = Point { lat: 51.51, lng: -0.13 };
        let dist = haversine_km(&nyc, &london);
        assert!((dist - 5570.0).abs() < 50.0, "NYC-London should be ~5570km, got {dist}");
    }

    #[test]
    fn test_same_location_zero_latency() {
        let p = Point { lat: 40.0, lng: -74.0 };
        let lat = estimate_latency_ms(&p, &p);
        assert!(lat <= 6.0, "Same location latency should be minimal, got {lat}");
    }

    #[test]
    fn test_nearby_location() {
        let p1 = Point { lat: 40.0, lng: -74.0 };
        let p2 = Point { lat: 40.5, lng: -74.5 };
        let lat = estimate_latency_ms(&p1, &p2);
        assert!(lat < 10.0, "Nearby locations should have low latency, got {lat}");
    }

    #[test]
    fn test_cross_atlantic_latency() {
        let nyc = Point { lat: 40.71, lng: -74.01 };
        let london = Point { lat: 51.5, lng: -0.1 };
        let lat = estimate_latency_ms(&nyc, &london);
        assert!(lat > 30.0, "Cross-Atlantic should be >30ms, got {lat}");
        assert!(lat < 100.0, "Cross-Atlantic should be <100ms, got {lat}");
    }

    #[test]
    fn test_cross_pacific_latency() {
        let sf = Point { lat: 37.77, lng: -122.42 };
        let tokyo = Point { lat: 35.68, lng: 139.69 };
        let lat = estimate_latency_ms(&sf, &tokyo);
        assert!(lat > 50.0, "Cross-Pacific should be >50ms, got {lat}");
        assert!(lat < 120.0, "Cross-Pacific should be <120ms, got {lat}");
    }

    #[test]
    fn test_latency_deterministic() {
        let p1 = Point { lat: 53.35, lng: -6.26 }; // Dublin
        let p2 = Point { lat: 64.0, lng: -22.5 }; // Iceland
        let a = estimate_latency_ms(&p1, &p2);
        let b = estimate_latency_ms(&p1, &p2);
        assert_eq!(a, b, "Latency estimation should be deterministic");
    }
}
