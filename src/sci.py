"""SCI (Software Carbon Intensity) calculation helpers.

Implements the Green Software Foundation formula:

    SCI = (E * I + M) / R

Where:
    E = Energy consumed (kWh)
    I = Location-based marginal carbon intensity (gCO2eq/kWh)
    M = Embodied emissions allocated to the usage window (gCO2)
    R = Functional unit count (e.g. inferences)
"""

import os
import warnings

import requests

# Hardware defaults (NVIDIA A100)
DEFAULT_TE_GCO2 = 150_000  # Total embodied CO2 in gCO2
DEFAULT_LIFESPAN_YEARS = 4
HOURS_PER_YEAR = 8760
DEFAULT_CARBON_INTENSITY = 475.0  # World average gCO2/kWh fallback


def compute_embodied_emissions(
    tir_hours: float,
    te_gco2: float = DEFAULT_TE_GCO2,
    lifespan_years: float = DEFAULT_LIFESPAN_YEARS,
) -> float:
    """Compute embodied emissions M = TE * (TiR / (Lifespan * 8760))."""
    return te_gco2 * (tir_hours / (lifespan_years * HOURS_PER_YEAR))


def compute_sci(
    energy_kwh: float,
    carbon_intensity: float,
    embodied_gco2: float,
    functional_units: int,
) -> float:
    """Compute SCI = (E * I + M) / R.

    Args:
        energy_kwh: Energy consumed in kWh (E).
        carbon_intensity: Marginal carbon intensity in gCO2eq/kWh (I).
        embodied_gco2: Embodied emissions for the usage window in gCO2 (M).
        functional_units: Number of functional units, e.g. inferences (R).

    Returns:
        SCI score in gCO2eq per functional unit.
    """
    if functional_units <= 0:
        raise ValueError("functional_units (R) must be > 0")
    return (energy_kwh * carbon_intensity + embodied_gco2) / functional_units


def get_carbon_intensity(api_key: str | None = None, zone: str | None = None) -> float:
    """Fetch live grid carbon intensity from the Electricity Maps API.

    Falls back to the world average (475 gCO2/kWh) on any failure.
    """
    api_key = api_key or os.environ.get("ELECTRICITY_MAPS_API_KEY", "")
    if not api_key:
        warnings.warn(
            "ELECTRICITY_MAPS_API_KEY not set. "
            f"Using world-average fallback: {DEFAULT_CARBON_INTENSITY} gCO2/kWh"
        )
        return DEFAULT_CARBON_INTENSITY

    url = "https://api.electricitymaps.com/v3/carbon-intensity/latest"
    headers = {"auth-token": api_key}
    params = {"zone": zone} if zone else {}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data["carbonIntensity"])
    except Exception as exc:
        warnings.warn(
            f"Electricity Maps API call failed ({exc}). "
            f"Using world-average fallback: {DEFAULT_CARBON_INTENSITY} gCO2/kWh"
        )
        return DEFAULT_CARBON_INTENSITY
