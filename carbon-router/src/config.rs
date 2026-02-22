use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub weights: WeightsConfig,
    pub carbon: CarbonConfig,
    pub health: HealthConfig,
    pub latency: LatencyConfig,
    #[serde(rename = "backend")]
    pub backends: Vec<BackendConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ServerConfig {
    pub bind: String,
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,
    #[serde(default = "default_body_limit")]
    pub max_body_bytes: usize,
}

fn default_timeout() -> u64 {
    300
}
fn default_body_limit() -> usize {
    10 * 1024 * 1024
}

#[derive(Debug, Clone, Deserialize)]
pub struct WeightsConfig {
    pub carbon: f64,
    pub latency: f64,
    pub cost: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CarbonConfig {
    #[serde(default = "default_poll_interval")]
    pub poll_interval_secs: u64,
    #[serde(default = "default_true")]
    pub use_simulated_fallback: bool,
    #[serde(default)]
    pub electricity_maps: Option<ElectricityMapsConfig>,
    #[serde(default)]
    pub watttime: Option<WattTimeConfig>,
    #[serde(default)]
    pub uk_carbon_intensity: Option<UkCarbonIntensityConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ElectricityMapsConfig {
    #[serde(default)]
    pub api_key: String,
    #[serde(default = "default_emaps_base_url")]
    pub base_url: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WattTimeConfig {
    #[serde(default)]
    pub username: String,
    #[serde(default)]
    pub password: String,
    #[serde(default = "default_watttime_base_url")]
    pub base_url: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UkCarbonIntensityConfig {
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_uk_base_url")]
    pub base_url: String,
}

fn default_emaps_base_url() -> String {
    "https://api.electricitymap.org/v3".to_string()
}
fn default_watttime_base_url() -> String {
    "https://api.watttime.org/v3".to_string()
}
fn default_uk_base_url() -> String {
    "https://api.carbonintensity.org.uk".to_string()
}
fn default_poll_interval() -> u64 {
    300
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
pub struct HealthConfig {
    #[serde(default = "default_health_interval")]
    pub interval_secs: u64,
    #[serde(default = "default_health_timeout")]
    pub timeout_secs: u64,
    #[serde(default = "default_unhealthy_threshold")]
    pub unhealthy_threshold: u32,
}

fn default_health_interval() -> u64 {
    30
}
fn default_health_timeout() -> u64 {
    5
}
fn default_unhealthy_threshold() -> u32 {
    3
}

#[derive(Debug, Clone, Deserialize)]
pub struct LatencyConfig {
    #[serde(default = "default_probe_interval")]
    pub probe_interval_secs: u64,
    #[serde(default = "default_probe_path")]
    pub probe_path: String,
}

fn default_probe_interval() -> u64 {
    60
}
fn default_probe_path() -> String {
    "/health".to_string()
}

fn default_auto() -> String {
    "auto".to_string()
}

#[derive(Debug, Clone, Deserialize)]
pub struct BackendConfig {
    pub id: String,
    pub name: String,
    pub url: String,
    pub lat: f64,
    pub lng: f64,
    pub power_type: PowerType,
    pub base_carbon: f64,
    #[serde(default)]
    pub electricity_maps_zone: String,
    #[serde(default)]
    pub gpus: Vec<String>,
    #[serde(default = "default_auto")]
    pub carbon_source: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum PowerType {
    Renewable,
    Hybrid,
    Grid,
    Ccs,
}

impl AppConfig {
    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("Failed to read config file {}: {}", path.display(), e))?;
        let config: AppConfig = toml::from_str(&content)
            .map_err(|e| anyhow::anyhow!("Failed to parse config: {}", e))?;
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> anyhow::Result<()> {
        let sum = self.weights.carbon + self.weights.latency + self.weights.cost;
        if (sum - 1.0).abs() > 0.01 {
            anyhow::bail!(
                "Weights must sum to 1.0, got {} + {} + {} = {}",
                self.weights.carbon,
                self.weights.latency,
                self.weights.cost,
                sum
            );
        }
        if self.backends.is_empty() {
            anyhow::bail!("At least one backend must be configured");
        }
        for b in &self.backends {
            if b.url.is_empty() {
                anyhow::bail!("Backend '{}' has an empty URL", b.id);
            }
            let valid_sources = ["auto", "electricity_maps", "watttime", "uk_carbon_intensity"];
            if !valid_sources.contains(&b.carbon_source.as_str()) {
                anyhow::bail!(
                    "Backend '{}' has invalid carbon_source '{}'. Valid: {:?}",
                    b.id,
                    b.carbon_source,
                    valid_sources
                );
            }
        }
        Ok(())
    }
}
