"""
CodeCarbon Demo — Track carbon emissions of a simple ML model
and compute the SCI (Software Carbon Intensity) score.

Trains a small neural network on a synthetic dataset using PyTorch while
CodeCarbon records energy usage and CO2 emissions, then calculates the
full SCI score per the Green Software Foundation formula:

    SCI = (E * I + M) / R
"""

import argparse
import os
import warnings

import requests
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.datasets import make_classification
from sklearn.model_selection import train_test_split
from codecarbon import EmissionsTracker

# SCI hardware defaults (NVIDIA A100)
DEFAULT_TE_GCO2 = 150_000  # Total embodied CO2 in gCO2
DEFAULT_LIFESPAN_YEARS = 4
HOURS_PER_YEAR = 8760
DEFAULT_CARBON_INTENSITY = 475.0  # World average gCO2/kWh fallback


# ---------------------------------------------------------------------------
# 1.  A small fully-connected neural network
# ---------------------------------------------------------------------------
class DemoNet(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_classes):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward(self, x):
        return self.net(x)


# ---------------------------------------------------------------------------
# 2.  Generate a synthetic classification dataset
# ---------------------------------------------------------------------------
def make_dataset(n_samples=50_000, n_features=100, n_classes=10, batch_size=256):
    X, y = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=80,
        n_classes=n_classes,
        random_state=42,
    )
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    def to_loader(X, y):
        ds = TensorDataset(
            torch.tensor(X, dtype=torch.float32),
            torch.tensor(y, dtype=torch.long),
        )
        return DataLoader(ds, batch_size=batch_size, shuffle=True)

    return to_loader(X_train, y_train), to_loader(X_test, y_test)


# ---------------------------------------------------------------------------
# 3.  Training loop
# ---------------------------------------------------------------------------
def train(model, loader, optimizer, criterion, device, epochs=10):
    model.train()
    for epoch in range(1, epochs + 1):
        total_loss = 0.0
        correct = 0
        total = 0
        for X_batch, y_batch in loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            logits = model(X_batch)
            loss = criterion(logits, y_batch)
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * X_batch.size(0)
            correct += (logits.argmax(1) == y_batch).sum().item()
            total += X_batch.size(0)

        avg_loss = total_loss / total
        accuracy = correct / total * 100
        print(f"  Epoch {epoch:>2}/{epochs}  —  loss: {avg_loss:.4f}  acc: {accuracy:.1f}%")


# ---------------------------------------------------------------------------
# 4.  Evaluation (returns accuracy and total inference count)
# ---------------------------------------------------------------------------
def evaluate(model, loader, device):
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for X_batch, y_batch in loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            preds = model(X_batch).argmax(1)
            correct += (preds == y_batch).sum().item()
            total += y_batch.size(0)
    return correct / total * 100, total


# ---------------------------------------------------------------------------
# 5.  Electricity Maps API — live carbon intensity
# ---------------------------------------------------------------------------
def get_carbon_intensity(api_key, zone=None):
    """Fetch live grid carbon intensity from the Electricity Maps API.

    Returns carbon intensity in gCO2eq/kWh.
    Falls back to the world average (475 gCO2/kWh) on any failure.
    """
    url = "https://api.electricitymaps.com/v3/carbon-intensity/latest"
    headers = {"auth-token": api_key}
    params = {}
    if zone:
        params["zone"] = zone

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        intensity = data["carbonIntensity"]
        print(f"  Carbon intensity from Electricity Maps ({data.get('zone', 'auto')}): "
              f"{intensity:.2f} gCO2eq/kWh")
        return intensity
    except Exception as exc:
        warnings.warn(
            f"Electricity Maps API call failed ({exc}). "
            f"Using world-average fallback: {DEFAULT_CARBON_INTENSITY} gCO2/kWh"
        )
        return DEFAULT_CARBON_INTENSITY


# ---------------------------------------------------------------------------
# 6.  SCI calculation helpers
# ---------------------------------------------------------------------------
def compute_embodied_emissions(te_gco2, tir_hours, lifespan_years):
    """Compute embodied emissions M = TE * (TiR / (Lifespan * 8760))."""
    return te_gco2 * (tir_hours / (lifespan_years * HOURS_PER_YEAR))


def compute_sci(E, I, M, R):
    """Compute SCI = (E * I + M) / R."""
    return (E * I + M) / R


# ---------------------------------------------------------------------------
# 7.  Main — run everything under CodeCarbon tracker, then compute SCI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="CodeCarbon + SCI demo")
    parser.add_argument("--zone", type=str, default=None,
                        help="Electricity Maps zone (e.g. 'DE', 'US-CAL-CISO'). "
                             "Auto-detected from IP if omitted.")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}\n")

    # --- Start the emissions tracker ---
    tracker = EmissionsTracker(
        project_name="demo-ml-carbon",
        output_dir="/home/saniya/Projects/hackeurope/hackeurope26/carbondemo",
        output_file="emissions.csv",
        log_level="warning",
    )
    tracker.start()

    try:
        print("Generating synthetic dataset (50 000 samples, 100 features, 10 classes)...")
        train_loader, test_loader = make_dataset()

        model = DemoNet(input_dim=100, hidden_dim=256, num_classes=10).to(device)
        optimizer = optim.Adam(model.parameters(), lr=1e-3)
        criterion = nn.CrossEntropyLoss()

        print("\nTraining for 20 epochs...\n")
        train(model, train_loader, optimizer, criterion, device, epochs=20)

        test_acc, R = evaluate(model, test_loader, device)
        print(f"\nTest accuracy: {test_acc:.1f}%")
        print(f"Test inferences (R): {R}")

    finally:
        emissions = tracker.stop()

    # --- Extract E and duration from the tracker ---
    E = tracker.final_emissions_data.energy_consumed  # kWh
    duration_seconds = tracker.final_emissions_data.duration   # seconds
    TiR_hours = duration_seconds / 3600.0

    # --- CodeCarbon report ---
    print("\n" + "=" * 60)
    print("  CODECARBON EMISSIONS REPORT")
    print("=" * 60)
    print(f"  Total emissions : {emissions:.6f} kg CO2eq")
    print(f"                  : {emissions * 1000:.4f} g CO2eq")
    print(f"  Energy consumed : {E:.6f} kWh")
    print(f"  Duration        : {duration_seconds:.2f} s ({TiR_hours:.6f} h)")
    print(f"  Results saved to: carbondemo/emissions.csv")
    print("=" * 60)

    # --- Fetch carbon intensity (I) ---
    print("\nFetching grid carbon intensity...")
    api_key = os.environ.get("ELECTRICITY_MAPS_API_KEY", "")
    if not api_key:
        warnings.warn(
            "ELECTRICITY_MAPS_API_KEY not set. "
            f"Using world-average fallback: {DEFAULT_CARBON_INTENSITY} gCO2/kWh"
        )
        I = DEFAULT_CARBON_INTENSITY
    else:
        I = get_carbon_intensity(api_key, zone=args.zone)

    # --- Compute embodied emissions (M) ---
    M = compute_embodied_emissions(DEFAULT_TE_GCO2, TiR_hours, DEFAULT_LIFESPAN_YEARS)

    # --- Compute SCI ---
    EI = E * I
    sci = compute_sci(E, I, M, R)

    # --- SCI report ---
    print("\n" + "=" * 60)
    print("  SCI SCORE REPORT")
    print("=" * 60)
    print(f"  E  (Energy consumed)      : {E:.6f} kWh")
    print(f"  I  (Carbon intensity)     : {I:.2f} gCO2/kWh")
    print(f"  M  (Embodied emissions)   : {M:.4f} gCO2")
    print(f"  R  (Functional units)     : {R} inferences")
    print("  " + "-" * 58)
    print(f"  E * I                     : {EI:.4f} gCO2")
    print(f"  SCI = (E*I + M) / R       : {sci:.6f} gCO2/inference")
    print("=" * 60)


if __name__ == "__main__":
    main()
