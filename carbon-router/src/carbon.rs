use crate::config::{CarbonConfig, PowerType};
use chrono::{Timelike, Utc};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{info, warn};

// ── Source Definitions ──────────────────────────────────────────────────────

/// All supported carbon data sources, dispatched via enum (no dyn).
#[derive(Clone)]
pub enum CarbonSourceKind {
    ElectricityMaps {
        api_key: String,
        base_url: String,
    },
    WattTime {
        base_url: String,
        username: String,
        password: String,
        /// Cached token: (token_string, obtained_at)
        token: Arc<RwLock<Option<(String, std::time::Instant)>>>,
    },
    UkCarbonIntensity {
        base_url: String,
    },
}

impl CarbonSourceKind {
    pub fn name(&self) -> &'static str {
        match self {
            Self::ElectricityMaps { .. } => "electricity_maps",
            Self::WattTime { .. } => "watttime",
            Self::UkCarbonIntensity { .. } => "uk_carbon_intensity",
        }
    }

    /// Whether this source can serve a given backend location.
    pub fn supports_backend(&self, _zone: &str, lat: f64, lng: f64) -> bool {
        match self {
            Self::ElectricityMaps { .. } => true, // global with zone
            Self::WattTime { .. } => true,        // global with region lookup
            Self::UkCarbonIntensity { .. } => is_gb(lat, lng),
        }
    }

    /// Fetch carbon intensity (gCO2eq/kWh) for a backend.
    pub async fn fetch_intensity(
        &self,
        client: &reqwest::Client,
        zone: &str,
        lat: f64,
        lng: f64,
        base_carbon: f64,
        watttime_regions: &RwLock<HashMap<String, String>>,
    ) -> Result<f64, String> {
        match self {
            Self::ElectricityMaps { api_key, base_url } => {
                fetch_electricity_maps(client, base_url, api_key, zone).await
            }
            Self::WattTime {
                base_url,
                username,
                password,
                token,
            } => {
                fetch_watttime(
                    client,
                    base_url,
                    username,
                    password,
                    token,
                    lat,
                    lng,
                    base_carbon,
                    watttime_regions,
                )
                .await
            }
            Self::UkCarbonIntensity { base_url } => {
                fetch_uk_carbon_intensity(client, base_url).await
            }
        }
    }
}

/// Check if coordinates fall within Great Britain.
fn is_gb(lat: f64, lng: f64) -> bool {
    (49.9..=60.9).contains(&lat) && (-8.2..=1.8).contains(&lng)
}

// ── ElectricityMaps ─────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct EmapsResponse {
    carbon_intensity: f64,
}

async fn fetch_electricity_maps(
    client: &reqwest::Client,
    base_url: &str,
    api_key: &str,
    zone: &str,
) -> Result<f64, String> {
    let url = format!("{}/carbon-intensity/latest?zone={}", base_url, zone);
    let resp = client
        .get(&url)
        .header("auth-token", api_key)
        .send()
        .await
        .map_err(|e| format!("ElectricityMaps request failed for zone {}: {}", zone, e))?;

    if !resp.status().is_success() {
        return Err(format!(
            "ElectricityMaps returned {} for zone {}",
            resp.status(),
            zone
        ));
    }

    let body: EmapsResponse = resp
        .json()
        .await
        .map_err(|e| format!("ElectricityMaps parse error for zone {}: {}", zone, e))?;

    Ok(body.carbon_intensity)
}

// ── WattTime ────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct WattTimeLoginResponse {
    token: String,
}

#[derive(Debug, Deserialize)]
struct WattTimeSignalResponse {
    data: Vec<WattTimeSignalEntry>,
}

#[derive(Debug, Deserialize)]
struct WattTimeSignalEntry {
    value: f64,
}

#[derive(Debug, Deserialize)]
struct WattTimeRegionResponse {
    region: String,
}

/// Get or refresh the WattTime bearer token (cached for 25 min).
async fn watttime_token(
    client: &reqwest::Client,
    base_url: &str,
    username: &str,
    password: &str,
    token_cache: &RwLock<Option<(String, std::time::Instant)>>,
) -> Result<String, String> {
    // Check cache
    {
        let cached = token_cache.read().await;
        if let Some((ref tok, obtained)) = *cached {
            if obtained.elapsed() < std::time::Duration::from_secs(25 * 60) {
                return Ok(tok.clone());
            }
        }
    }

    // Login
    let url = format!("{}/login", base_url);
    let resp = client
        .get(&url)
        .basic_auth(username, Some(password))
        .send()
        .await
        .map_err(|e| format!("WattTime login failed: {}", e))?;

    if !resp.status().is_success() {
        return Err(format!("WattTime login returned {}", resp.status()));
    }

    let body: WattTimeLoginResponse = resp
        .json()
        .await
        .map_err(|e| format!("WattTime login parse error: {}", e))?;

    let tok = body.token.clone();
    let mut cached = token_cache.write().await;
    *cached = Some((body.token, std::time::Instant::now()));
    Ok(tok)
}

/// Resolve WattTime region from lat/lng (cached per backend key).
async fn watttime_region(
    client: &reqwest::Client,
    base_url: &str,
    token: &str,
    lat: f64,
    lng: f64,
    regions: &RwLock<HashMap<String, String>>,
) -> Result<String, String> {
    let key = format!("{:.2},{:.2}", lat, lng);
    {
        let cache = regions.read().await;
        if let Some(r) = cache.get(&key) {
            return Ok(r.clone());
        }
    }

    let url = format!(
        "{}/region-from-loc?latitude={}&longitude={}&signal_type=co2_moer",
        base_url, lat, lng
    );
    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| format!("WattTime region lookup failed: {}", e))?;

    if !resp.status().is_success() {
        return Err(format!("WattTime region lookup returned {}", resp.status()));
    }

    let body: WattTimeRegionResponse = resp
        .json()
        .await
        .map_err(|e| format!("WattTime region parse error: {}", e))?;

    let region = body.region.clone();
    let mut cache = regions.write().await;
    cache.insert(key, body.region);
    Ok(region)
}

async fn fetch_watttime(
    client: &reqwest::Client,
    base_url: &str,
    username: &str,
    password: &str,
    token_cache: &RwLock<Option<(String, std::time::Instant)>>,
    lat: f64,
    lng: f64,
    base_carbon: f64,
    regions: &RwLock<HashMap<String, String>>,
) -> Result<f64, String> {
    let token = watttime_token(client, base_url, username, password, token_cache).await?;
    let region = watttime_region(client, base_url, &token, lat, lng, regions).await?;

    let url = format!("{}/signal-index?region={}", base_url, region);
    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("WattTime signal failed for region {}: {}", region, e))?;

    if !resp.status().is_success() {
        return Err(format!(
            "WattTime signal returned {} for region {}",
            resp.status(),
            region
        ));
    }

    let body: WattTimeSignalResponse = resp
        .json()
        .await
        .map_err(|e| format!("WattTime signal parse error: {}", e))?;

    let percentile = body
        .data
        .first()
        .map(|e| e.value)
        .ok_or_else(|| "WattTime signal returned empty data".to_string())?;

    Ok(watttime_percentile_to_carbon(percentile, base_carbon))
}

/// Convert WattTime percentile (0-100) to estimated gCO2/kWh.
/// Lower percentile = cleaner grid.
pub fn watttime_percentile_to_carbon(percentile: f64, base_carbon: f64) -> f64 {
    base_carbon * (0.5 + percentile / 100.0)
}

// ── UK Carbon Intensity ─────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct UkCarbonResponse {
    data: Vec<UkCarbonDataEntry>,
}

#[derive(Debug, Deserialize)]
struct UkCarbonDataEntry {
    intensity: UkIntensity,
}

#[derive(Debug, Deserialize)]
struct UkIntensity {
    actual: Option<f64>,
    forecast: f64,
}

async fn fetch_uk_carbon_intensity(
    client: &reqwest::Client,
    base_url: &str,
) -> Result<f64, String> {
    let url = format!("{}/intensity", base_url);
    let resp = client
        .get(&url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|e| format!("UK Carbon Intensity request failed: {}", e))?;

    if !resp.status().is_success() {
        return Err(format!(
            "UK Carbon Intensity returned {}",
            resp.status()
        ));
    }

    let body: UkCarbonResponse = resp
        .json()
        .await
        .map_err(|e| format!("UK Carbon Intensity parse error: {}", e))?;

    let entry = body
        .data
        .first()
        .ok_or_else(|| "UK Carbon Intensity returned empty data".to_string())?;

    Ok(entry.intensity.actual.unwrap_or(entry.intensity.forecast))
}

// ── Simulated Fallback ──────────────────────────────────────────────────────

/// Simulated carbon intensity fallback (ported from JSX lines 73-80).
pub fn simulate_carbon(base_carbon: f64, power_type: &PowerType) -> f64 {
    let hour = Utc::now().hour() as f64;

    match power_type {
        PowerType::Renewable => base_carbon.max(0.0),
        PowerType::Ccs => base_carbon + 5.0,
        PowerType::Grid | PowerType::Hybrid => {
            let time_multiplier =
                0.7 + 0.6 * (((hour - 6.0) / 24.0) * std::f64::consts::PI * 2.0).sin();
            (base_carbon * time_multiplier).max(50.0)
        }
    }
}

// ── Source Construction ─────────────────────────────────────────────────────

/// Build the ordered list of available carbon sources from config.
pub fn build_sources(config: &CarbonConfig) -> Vec<CarbonSourceKind> {
    let mut sources = Vec::new();

    // UK Carbon Intensity (checked first in auto mode for GB backends)
    if let Some(ref uk) = config.uk_carbon_intensity {
        if uk.enabled {
            sources.push(CarbonSourceKind::UkCarbonIntensity {
                base_url: uk.base_url.clone(),
            });
        }
    }

    // WattTime
    if let Some(ref wt) = config.watttime {
        if !wt.username.is_empty() && !wt.password.is_empty() {
            sources.push(CarbonSourceKind::WattTime {
                base_url: wt.base_url.clone(),
                username: wt.username.clone(),
                password: wt.password.clone(),
                token: Arc::new(RwLock::new(None)),
            });
        }
    }

    // ElectricityMaps
    if let Some(ref em) = config.electricity_maps {
        if !em.api_key.is_empty() {
            sources.push(CarbonSourceKind::ElectricityMaps {
                api_key: em.api_key.clone(),
                base_url: em.base_url.clone(),
            });
        }
    }

    sources
}

/// Select sources for a backend: if `carbon_source` is a specific name, filter to that;
/// otherwise ("auto"), return all that support the backend's location.
fn sources_for_backend<'a>(
    all_sources: &'a [CarbonSourceKind],
    carbon_source: &str,
    zone: &str,
    lat: f64,
    lng: f64,
) -> Vec<&'a CarbonSourceKind> {
    if carbon_source == "auto" {
        all_sources
            .iter()
            .filter(|s| s.supports_backend(zone, lat, lng))
            .collect()
    } else {
        all_sources
            .iter()
            .filter(|s| s.name() == carbon_source)
            .collect()
    }
}

// ── Zone Mapping ────────────────────────────────────────────────────────────

pub fn zone_for_backend(backend_id: &str) -> &'static str {
    match backend_id {
        "eu-iceland1-a" => "IS",
        "eu-norway1-a" => "NO-NO1",
        "us-east1-a" => "US-MIDA-PJM",
        "us-southcentral1-a" => "US-TEX-ERCO",
        "us-northcentral1-a" => "US-NW-NWMT",
        "us-tx-abilene" => "US-TEX-ERCO",
        "us-nv-sparks" => "US-NW-PACW",
        "us-wy-cheyenne" => "US-NW-WACM",
        "ca-ab-calgary" => "CA-AB",
        _ => "US-MIDA-PJM",
    }
}

// ── Poller ───────────────────────────────────────────────────────────────────

pub async fn carbon_poller(state: Arc<crate::state::AppState>) {
    let poll_interval = std::time::Duration::from_secs(state.config.carbon.poll_interval_secs);
    let use_simulated = state.config.carbon.use_simulated_fallback;

    let sources = build_sources(&state.config.carbon);
    let watttime_regions: Arc<RwLock<HashMap<String, String>>> =
        Arc::new(RwLock::new(HashMap::new()));

    if sources.is_empty() && !use_simulated {
        warn!("No carbon sources configured and simulated fallback disabled — carbon values will not update");
    }

    // Collect backend info
    let backend_info: Vec<(String, String, f64, f64, f64, PowerType, String)> = {
        let backends = state.backends.read().await;
        backends
            .values()
            .map(|b| {
                let zone = if b.config.electricity_maps_zone.is_empty() {
                    zone_for_backend(&b.config.id).to_string()
                } else {
                    b.config.electricity_maps_zone.clone()
                };
                (
                    b.config.id.clone(),
                    zone,
                    b.config.lat,
                    b.config.lng,
                    b.config.base_carbon,
                    b.config.power_type.clone(),
                    b.config.carbon_source.clone(),
                )
            })
            .collect()
    };

    loop {
        if use_simulated && sources.is_empty() {
            // Pure simulated mode
            let mut backends = state.backends.write().await;
            for b in backends.values_mut() {
                b.current_carbon = simulate_carbon(b.config.base_carbon, &b.config.power_type);
                b.carbon_last_updated = Some(Utc::now());
                b.carbon_source = "simulated".to_string();
            }
            info!(
                "Updated carbon intensity (simulated) for {} backends",
                backends.len()
            );
        } else {
            // Per-backend: try sources in order, fall back to simulated
            for (id, zone, lat, lng, base_carbon, power_type, carbon_source_pref) in &backend_info
            {
                let candidates =
                    sources_for_backend(&sources, carbon_source_pref, zone, *lat, *lng);

                let mut success = false;
                for source in &candidates {
                    // Rate-limit delay for ElectricityMaps only
                    if source.name() == "electricity_maps" {
                        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                    }

                    match source
                        .fetch_intensity(
                            &state.client,
                            zone,
                            *lat,
                            *lng,
                            *base_carbon,
                            &watttime_regions,
                        )
                        .await
                    {
                        Ok(carbon) => {
                            let mut backends = state.backends.write().await;
                            if let Some(b) = backends.get_mut(id.as_str()) {
                                b.current_carbon = carbon;
                                b.carbon_last_updated = Some(Utc::now());
                                b.carbon_source = source.name().to_string();
                            }
                            info!(
                                backend = %id,
                                source = source.name(),
                                carbon = %carbon,
                                "Fetched carbon intensity"
                            );
                            success = true;
                            break;
                        }
                        Err(e) => {
                            warn!(
                                backend = %id,
                                source = source.name(),
                                error = %e,
                                "Carbon source failed, trying next"
                            );
                        }
                    }
                }

                if !success {
                    // Fallback to simulated
                    let simulated = simulate_carbon(*base_carbon, power_type);
                    let mut backends = state.backends.write().await;
                    if let Some(b) = backends.get_mut(id.as_str()) {
                        b.current_carbon = simulated;
                        b.carbon_last_updated = Some(Utc::now());
                        b.carbon_source = "simulated".to_string();
                    }
                    if !candidates.is_empty() {
                        warn!(
                            backend = %id,
                            "All carbon sources failed, using simulated fallback"
                        );
                    }
                }
            }
        }

        tokio::time::sleep(poll_interval).await;
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_watttime_percentile_conversion() {
        // Percentile 0 = cleanest → 50% of base
        assert!((watttime_percentile_to_carbon(0.0, 400.0) - 200.0).abs() < 0.01);
        // Percentile 50 → 100% of base
        assert!((watttime_percentile_to_carbon(50.0, 400.0) - 400.0).abs() < 0.01);
        // Percentile 100 = dirtiest → 150% of base
        assert!((watttime_percentile_to_carbon(100.0, 400.0) - 600.0).abs() < 0.01);
    }

    #[test]
    fn test_is_gb() {
        // London
        assert!(is_gb(51.5, -0.1));
        // Edinburgh
        assert!(is_gb(55.95, -3.19));
        // Paris — not GB
        assert!(!is_gb(48.85, 2.35));
        // Iceland — not GB
        assert!(!is_gb(64.0, -22.5));
    }

    #[test]
    fn test_simulate_carbon_renewable() {
        let c = simulate_carbon(12.0, &PowerType::Renewable);
        assert!((c - 12.0).abs() < 0.01);
    }

    #[test]
    fn test_simulate_carbon_ccs() {
        let c = simulate_carbon(45.0, &PowerType::Ccs);
        assert!((c - 50.0).abs() < 0.01);
    }
}
