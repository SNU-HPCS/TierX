#!/usr/bin/env python3
"""
Single entrypoint to generate all YAML configuration files for every application.
All workload-specific generators are inlined here to keep config generation in one
place while preserving the same outputs as the former per-workload scripts.
"""

import argparse
import copy
import os
import sys
from itertools import product
from typing import Dict, Iterable, List, Tuple

import yaml


# Shared constants for energy storage calculations
MAX_LIFETIME_HRS = 18
CHARGE_TIME_HRS = 6
RADIATED_POWER_MW = 11  # near-implant to implant
MIN_CHARGE_STATUS = 0
CAP_CHARGED_ENERGY_WH = RADIATED_POWER_MW / 1000 * 10 ** (-10 / 10) * 0.9 * 0.95 * CHARGE_TIME_HRS
CAP_INITIAL_CHARGE = min(CAP_CHARGED_ENERGY_WH / 0.0002 * 100 + MIN_CHARGE_STATUS, 100)
BAT_CHARGED_ENERGY_WH = RADIATED_POWER_MW / 1000 * 10 ** (-10 / 10) * 0.9 * 0.8 * CHARGE_TIME_HRS
SMALL_BAT_INITIAL_CHARGE = min(BAT_CHARGED_ENERGY_WH / 0.2 * 100 + MIN_CHARGE_STATUS, 100)
BAT_INITIAL_CHARGE = min(BAT_CHARGED_ENERGY_WH / 2 * 100 + MIN_CHARGE_STATUS, 100)


def load_base_config(base_file: str) -> dict:
    """Load the base YAML configuration file."""
    with open(base_file, "r") as f:
        return yaml.safe_load(f)


def get_hardcoded_transceiver_configs() -> Dict[str, dict]:
    """Return hardcoded transceiver configurations shared by all workloads."""
    def _trx(method: str, frequency: int, bandwidth: int, data_rate: int,
             dynamic_power: float, radiated_power: int) -> dict:
        return {
            "type": "TRX", "method": method, "modulation": "BPSK",
            "frequency": frequency, "bandwidth": bandwidth, "data_rate": data_rate,
            "dynamic_power": dynamic_power, "radiated_power": radiated_power,
            "static_power": 0, "noise_figure": 10,
        }
    return {
        "LOW-RF": _trx("RF", 900, 2, 20, 4.0, 10),
        "LOW-BCC": _trx("BCC", 40, 40, 20, 1.6, 1),
        "HIGH-BCC": _trx("BCC", 40, 40, 80, 6.4, 1),
    }


def _make_energy_storage(storage_type: str, weight: float, energy_density: int,
                         efficiency: float, initial_charge: float) -> dict:
    """Create an energy storage config with common defaults."""
    return {
        "type": storage_type,
        "weight": weight,
        "energy_density": energy_density,
        "round_trip_efficiency": efficiency,
        "self_discharge_rate": 0.1,
        "initial_charge": initial_charge,
        "charge_time": CHARGE_TIME_HRS,
        "max_lifetime": MAX_LIFETIME_HRS,
        "min_charge_status": MIN_CHARGE_STATUS,
        "v_max": 4.2,
        "coulombic_efficiency": 1,
    }


# Shared energy configs for near_implant_0 and off_implant_0 (same across all energy types)
_NEAR_IMPLANT_ENERGY = _make_energy_storage("battery", 0.1, 100, 0.8, 100)
_OFF_IMPLANT_ENERGY = _make_energy_storage("battery", 0.5, 100, 0.8, 100)


def get_configuration_options(transceiver_configs: Dict[str, dict]):
    """Define location, communication, and energy configurations."""
    location_configs = {
        "TEMPLE-ARM": {"near_implant_0": "temple", "off_implant_0": "arm"},
        "NECK-ARM": {"near_implant_0": "neck", "off_implant_0": "arm"},
        "NECK-EXTERNAL": {"near_implant_0": "neck", "off_implant_0": "external"},
    }

    # Build comm_configs dynamically for each transceiver type
    comm_configs = {
        name: {f"{node}_comm": {"transceivers": [copy.deepcopy(trx)]}
               for node in ["implant_0", "near_implant_0", "off_implant_0"]}
        for name, trx in transceiver_configs.items()
    }

    # Implant-specific energy configs (only implant_0 varies per energy type)
    implant_configs = {
        "SMALL-BAT": _make_energy_storage("battery", 0.002, 100, 0.8, SMALL_BAT_INITIAL_CHARGE),
        "BAT": _make_energy_storage("battery", 0.02, 100, 0.8, BAT_INITIAL_CHARGE),
        "SMALL-CAP": _make_energy_storage("supercapacitor", 0.0001, 2, 0.95, CAP_INITIAL_CHARGE),
        "OFF": _make_energy_storage("battery", 0.02, 100, 0.8, 100),
    }

    energy_configs = {
        name: {
            "implant_0": implant_cfg,
            "near_implant_0": copy.deepcopy(_NEAR_IMPLANT_ENERGY),
            "off_implant_0": copy.deepcopy(_OFF_IMPLANT_ENERGY),
        }
        for name, implant_cfg in implant_configs.items()
    }

    return location_configs, comm_configs, energy_configs


def get_power_management_configs() -> Dict[str, dict]:
    """Define power-management transceiver configurations shared across energy types."""
    fixed_power_config = {
        "implant_0": {"transceivers": [
            {"type": "RX", "method": "Inductive", "frequency": 13, "bandwidth": 1, "rectification_efficiency": 0.9},
            {"type": "TX", "method": "none"},
        ]},
        "near_implant_0": {"transceivers": [
            {"type": "TX", "method": "Inductive", "frequency": 13, "bandwidth": 1,
             "static_power": 0, "dynamic_power": 30, "radiated_power": RADIATED_POWER_MW},
            {"type": "RX", "method": "RF", "frequency": 900, "bandwidth": 1, "rectification_efficiency": 0.9},
        ]},
        "off_implant_0": {"transceivers": [
            {"type": "TX", "method": "RF", "frequency": 900, "bandwidth": 1,
             "static_power": 0, "dynamic_power": 50, "radiated_power": 40},
            {"type": "RX", "method": "none"},
        ]},
    }
    return {energy_type: copy.deepcopy(fixed_power_config)
            for energy_type in ["SMALL-BAT", "BAT", "SMALL-CAP", "OFF"]}


def get_processor_configs() -> Dict[str, dict]:
    """Placeholder processor configs (kept empty to match prior behavior)."""
    return {"implant_0": {}, "near_implant_0": {}, "off_implant_0": {}}


def apply_configuration(
    base_config: dict,
    location_name: str,
    comm_name: str,
    energy_name: str,
    location_configs: Dict[str, dict],
    comm_configs: Dict[str, dict],
    energy_configs: Dict[str, dict],
    power_configs: Dict[str, dict],
    processor_configs: Dict[str, dict],
) -> dict:
    """Apply specific configuration to a deep copy of the base config."""
    config = copy.deepcopy(base_config)

    location_config = location_configs[location_name]
    for node in config["hardware_spec"]["nodes"]:
        node_name = node["name"]
        if node_name in location_config:
            node["location"] = location_config[node_name]

    comm_config = comm_configs[comm_name]
    for node in config["hardware_spec"]["nodes"]:
        node_name = node["name"]
        comm_key = f"{node_name}_comm"
        if comm_key in comm_config:
            node["comm_link"]["transceivers"] = comm_config[comm_key]["transceivers"]

    energy_config = energy_configs[energy_name]
    power_config = power_configs.get(energy_name, power_configs["BAT"])
    for node in config["hardware_spec"]["nodes"]:
        node_name = node["name"]
        if node_name in energy_config:
            node["power_management"]["energy_storage"] = energy_config[node_name]
        if node_name in power_config:
            node["power_management"]["transceivers"] = power_config[node_name]["transceivers"]

    # processor_configs remain unused (kept for parity with prior scripts)

    config["environment"]["realtime_charging"] = energy_name != "OFF"
    return config


def generate_config_files(base_file: str, file_suffix: str, output_dir: str | None = None) -> List[str]:
    """Generate all configuration files for a given workload."""
    if output_dir is None:
        output_dir = os.path.dirname(base_file)

    base_config = load_base_config(base_file)
    transceiver_configs = get_hardcoded_transceiver_configs()
    location_configs, comm_configs, energy_configs = get_configuration_options(transceiver_configs)
    power_configs = get_power_management_configs()
    processor_configs = get_processor_configs()

    generated_files: List[str] = []
    for location_name, comm_name, energy_name in product(location_configs.keys(), comm_configs.keys(), energy_configs.keys()):
        config = apply_configuration(
            base_config,
            location_name,
            comm_name,
            energy_name,
            location_configs,
            comm_configs,
            energy_configs,
            power_configs,
            processor_configs,
        )

        filename = f"{location_name}_{comm_name}_{energy_name}_{file_suffix}.yaml"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, indent=2)
        generated_files.append(filename)
        print(f"Generated: {filename}")

    return generated_files


# app_name -> (base_file, file_suffix, description)
APP_GENERATORS: Dict[str, Tuple[str, str, str]] = {
    "NN": ("lib/Input/NN/BASE_NN.yaml", "NN", "Neural Network"),
    "GRU": ("lib/Input/GRU/BASE_GRU.yaml", "GRU", "Gated Recurrent Unit"),
    "Seizure": ("lib/Input/Seizure/BASE_Seizure.yaml", "Seizure", "Seizure Detection"),
    "SpikeSorting": ("lib/Input/SpikeSorting/BASE_SpikeSorting.yaml", "SpikeSorting", "Spike Sorting"),
}


def generate(apps: Iterable[str], output_dir: str = None) -> Dict[str, Tuple[bool, List[str] | str]]:
    """Run selected generators and return a summary per app."""
    summary: Dict[str, Tuple[bool, List[str] | str]] = {}
    for app in apps:
        base_file, suffix, desc = APP_GENERATORS[app]
        if not os.path.exists(base_file):
            msg = f"Missing base file: {base_file}"
            print(f"[{app}] {msg}")
            summary[app] = (False, msg)
            continue
        try:
            files = generate_config_files(base_file, suffix, output_dir)
            summary[app] = (True, files)
            print(f"[{app}] {desc}: generated {len(files)} files")
        except Exception as exc:  # keep concise error surface
            summary[app] = (False, str(exc))
            print(f"[{app}] failed: {exc}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Generate YAML configs for all applications")
    parser.add_argument(
        "--apps",
        nargs="+",
        choices=sorted(APP_GENERATORS.keys()),
        help="Subset of applications to generate (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        help="Override output directory for generated YAMLs (default: alongside base file)",
    )
    args = parser.parse_args()

    target_apps = args.apps or list(APP_GENERATORS.keys())
    print(f"Generating configs for: {', '.join(target_apps)}")

    summary = generate(target_apps, args.output_dir)
    successes = [app for app, (ok, _) in summary.items() if ok]
    failures = {app: msg for app, (ok, msg) in summary.items() if not ok}

    print("\nSummary:")
    for app in target_apps:
        ok, info = summary.get(app, (False, "not run"))
        status = "OK" if ok else "FAIL"
        detail = f"{len(info)} files" if ok and isinstance(info, list) else str(info)
        print(f"  {app:13} {status:4} {detail}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
