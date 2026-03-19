#!/usr/bin/env python3
"""Compute TierX DSE statistics for a run.

This script is intended to be called by run.sh at the end of a run.
It mirrors how run.sh constructs workloads and how DSE.py expands them into tasks.

Key idea:
- "Requested tasks" counts every (workload, component_file, electrode, charge_time, metric) evaluation.
- "Unique configs" deduplicates across metrics when the simulated configuration would be identical.

Notes:
- DSE.py skips workloads containing both 'BCC' and 'EXTERNAL'. We apply the same rule.
- Processor DSE doesn't simulate; it generates workload YAMLs. We count those generations.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import yaml


@dataclass(frozen=True)
class RunParams:
    applications: Tuple[str, ...]
    optimize_metrics: Tuple[str, ...]
    sweep_types: Tuple[str, ...]
    communication_methods: Tuple[str, ...]
    power_sources: Tuple[str, ...]
    node_placements: Tuple[str, ...]


def _read_yaml_list(path: str, key: str) -> List[str]:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    value = cfg.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"Expected list for '{key}' in {path}, got {type(value).__name__}")
    return [str(x) for x in value]


def load_tierx_params(path: str) -> RunParams:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    def get_list(name: str) -> Tuple[str, ...]:
        value = cfg.get(name, [])
        if value is None:
            return tuple()
        if not isinstance(value, list):
            raise TypeError(f"Expected list for '{name}' in {path}, got {type(value).__name__}")
        return tuple(str(x) for x in value)

    return RunParams(
        applications=get_list("applications"),
        optimize_metrics=get_list("optimize_metrics"),
        sweep_types=get_list("sweep_types"),
        communication_methods=get_list("communication_methods"),
        power_sources=get_list("power_sources"),
        node_placements=get_list("node_placements"),
    )


def iter_base_workloads(params: RunParams, application: str) -> Iterable[str]:
    for node_placement, comm_method, power_source in product(
        params.node_placements, params.communication_methods, params.power_sources
    ):
        yield f"{node_placement}_{comm_method}_{power_source}_{application}"


def list_processor_split_suffixes(application: str) -> List[str]:
    base_dir = os.path.join("lib", "Input", "HW_components", "processor", application)
    if not os.path.isdir(base_dir):
        return []

    suffixes: List[str] = []
    for fname in sorted(os.listdir(base_dir)):
        if not fname.endswith(".yaml"):
            continue
        if not fname.startswith("processor_"):
            continue
        # Matches run.sh behavior:
        workload_suffix = fname[len("processor_") : -len(".yaml")]
        formatted_suffix = workload_suffix.replace("_", "")
        suffixes.append(formatted_suffix)
    return suffixes


def iter_split_workloads(params: RunParams, application: str, suffixes: Sequence[str]) -> Iterable[str]:
    for suffix in suffixes:
        for node_placement, comm_method, power_source in product(
            params.node_placements, params.communication_methods, params.power_sources
        ):
            yield f"{node_placement}_{comm_method}_{power_source}_{application}_{suffix}"


def should_skip_workload(workload: str) -> bool:
    return ("BCC" in workload) and ("EXTERNAL" in workload)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compute TierX DSE stats")
    p.add_argument("--tierx", default="TierX.yaml", help="Path to TierX.yaml")
    # Default to env variable if set, otherwise SearchSpace.yaml
    default_searchspace = os.environ.get('SEARCH_SPACE_CONFIG', 'SearchSpace.yaml')
    p.add_argument("--searchspace", default=default_searchspace, help="Path to SearchSpace.yaml")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p.add_argument("--no-timing", action="store_true", help="Skip reading timing logs (data_DSE/timing)")
    p.add_argument("--pre-run", action="store_true", help="Show estimated stats before execution (skip timing)")
    p.add_argument("--search", choices=['exhaustive', 'ga', 'pruning'], default='exhaustive', help="Search strategy to use for GA task estimation")
    return p.parse_args()


def _safe_percentile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    if q <= 0:
        return float(sorted_values[0])
    if q >= 1:
        return float(sorted_values[-1])
    idx = int(round((len(sorted_values) - 1) * q))
    idx = max(0, min(len(sorted_values) - 1, idx))
    return float(sorted_values[idx])


def _load_timing_records() -> List[dict]:
    base_dir = os.path.join('data_DSE', 'timing')
    if not os.path.isdir(base_dir):
        return []
    records: List[dict] = []
    for fname in sorted(os.listdir(base_dir)):
        if not fname.endswith('.jsonl'):
            continue
        path = os.path.join(base_dir, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
    return records


def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _collect_list_knobs(obj: Any, prefix: str = '') -> List[Tuple[str, int]]:
    """Collect (path, option_count) for any list-valued leaf nodes.

    Example output key: "dse.electrodes.throughput.default" -> 12
    """
    knobs: List[Tuple[str, int]] = []

    if isinstance(obj, list):
        key = prefix.strip('.') or '<root>'
        knobs.append((key, len(obj)))
        return knobs

    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                continue
            knobs.extend(_collect_list_knobs(v, prefix=f'{prefix}{k}.'))
    return knobs


def _format_ms(value_s: Optional[float]) -> str:
    if value_s is None:
        return 'n/a'
    return f'{value_s * 1000.0:.2f}ms'


def main() -> int:
    args = parse_args()

    params = load_tierx_params(args.tierx)

    # Import DSE helpers so we stay consistent with electrode/charge-time selection.
    # (We avoid running any simulation; we only call pure helper functions.)
    import src.DSE as dse  # type: ignore

    # Force DSE to (re)load search space from the same file run.sh would use.
    # DSE.py loads at import time; overwrite if caller points elsewhere.
    # This keeps stats consistent if user passes --searchspace.
    if args.searchspace:
        # DSE's loader is safe; it returns {} if missing.
        dse.search_space = dse.load_search_space(args.searchspace)

    sweep_to_component = {
        "communication": "trx",
        "power": "power",
        "node": "env",
    }

    # Aggregate stats
    dse_invocations = 0
    processor_generations_requested = 0
    processor_generations_skipped = 0

    requested_tasks_total = 0
    unique_tasks_with_metric: Set[Tuple[str, str, str, str, str, int, int]] = set()
    unique_tasks_config_only: Set[Tuple[str, str, str, str, int, int]] = set()

    # Per-application breakdown
    requested_tasks_by_app: Dict[str, int] = {}
    unique_with_metric_by_app: Dict[str, Set[Tuple[str, str, str, str, str, int, int]]] = {}
    unique_config_only_by_app: Dict[str, Set[Tuple[str, str, str, str, int, int]]] = {}

    workload_total = 0
    workload_skipped = 0

    for app in params.applications:
        # Processor stage: base workloads (no split suffix)
        base_workloads = list(iter_base_workloads(params, app))
        workload_total += len(base_workloads)
        base_workloads_kept = [w for w in base_workloads if not should_skip_workload(w)]
        workload_skipped += len(base_workloads) - len(base_workloads_kept)

        # Count processor YAML generations: one per (base workload, processor split config)
        suffixes = list_processor_split_suffixes(app)
        proc_cfg_count = len(suffixes)

        if proc_cfg_count > 0:
            # run.sh calls DSE.py once for processor per application (unless graph-only)
            dse_invocations += 1

        for w in base_workloads:
            if should_skip_workload(w):
                processor_generations_skipped += proc_cfg_count
            else:
                processor_generations_requested += proc_cfg_count

        # Sweep stages: workloads include processor suffix
        split_workloads = list(iter_split_workloads(params, app, suffixes))
        # Note: these are additional workload strings used for DSE sweeps
        workload_total += len(split_workloads)
        split_workloads_kept = [w for w in split_workloads if not should_skip_workload(w)]
        workload_skipped += len(split_workloads) - len(split_workloads_kept)

        for sweep in params.sweep_types:
            comp_type = sweep_to_component.get(sweep)
            if comp_type is None:
                # If run.sh validated, this shouldn't happen.
                continue

            for metric in params.optimize_metrics:
                # One DSE invocation per (app, sweep, metric)
                dse_invocations += 1

                for workload in split_workloads_kept:
                    electrodes = dse.select_electrodes(app, comp_type, metric)
                    charge_times = dse.select_charge_times(comp_type, metric)
                    component_files = dse.load_component_files(comp_type, app, workload)

                    # Requested tasks for this workload
                    count = len(component_files) * len(electrodes) * len(charge_times)
                    requested_tasks_total += count
                    requested_tasks_by_app[app] = requested_tasks_by_app.get(app, 0) + count

                    for cf, e, ct in product(component_files, electrodes, charge_times):
                        unique_tasks_with_metric.add((app, comp_type, metric, workload, cf, int(e), int(ct)))
                        unique_tasks_config_only.add((app, comp_type, workload, cf, int(e), int(ct)))

                        unique_with_metric_by_app.setdefault(app, set()).add(
                            (app, comp_type, metric, workload, cf, int(e), int(ct))
                        )
                        unique_config_only_by_app.setdefault(app, set()).add(
                            (app, comp_type, workload, cf, int(e), int(ct))
                        )

    timing_records: List[dict] = []
    timing_summary: Dict[str, object] = {}
    timing_by_group: Dict[str, Dict[str, object]] = {}
    if not args.no_timing:
        timing_records = _load_timing_records()
        net_runs = [float(r.get('net_run_s', 0.0)) for r in timing_records if isinstance(r.get('net_run_s'), (int, float))]
        eval_totals = [float(r.get('eval_total_s', 0.0)) for r in timing_records if isinstance(r.get('eval_total_s'), (int, float))]
        net_runs_sorted = sorted(net_runs)
        eval_totals_sorted = sorted(eval_totals)

        def summarize(values_sorted: List[float]) -> Dict[str, Optional[float]]:
            if not values_sorted:
                return {'count': 0, 'sum_s': 0.0, 'mean_s': None, 'p50_s': None, 'p95_s': None, 'p99_s': None, 'max_s': None}
            total = float(sum(values_sorted))
            return {
                'count': int(len(values_sorted)),
                'sum_s': total,
                'mean_s': float(total / len(values_sorted)),
                'p50_s': _safe_percentile(values_sorted, 0.50),
                'p95_s': _safe_percentile(values_sorted, 0.95),
                'p99_s': _safe_percentile(values_sorted, 0.99),
                'max_s': float(values_sorted[-1]),
            }

        timing_summary = {
            'net_run': summarize(net_runs_sorted),
            'eval_total': summarize(eval_totals_sorted),
        }

        # Group by (component_type, optimize_metric)
        groups: Dict[Tuple[str, str], List[float]] = {}
        for r in timing_records:
            comp = str(r.get('component_type', 'unknown'))
            metric = str(r.get('optimize_metric', 'None'))
            t = r.get('net_run_s')
            if not isinstance(t, (int, float)):
                continue
            groups.setdefault((comp, metric), []).append(float(t))

        for (comp, metric), vals in sorted(groups.items()):
            vals_sorted = sorted(vals)
            timing_by_group[f'{comp}:{metric}'] = {
                'count': int(len(vals_sorted)),
                'mean_ms': float(sum(vals_sorted) / len(vals_sorted) * 1000.0) if vals_sorted else None,
                'p95_ms': float((_safe_percentile(vals_sorted, 0.95) or 0.0) * 1000.0) if vals_sorted else None,
            }

        # Per-application timing summary
        timing_by_app: Dict[str, Dict[str, object]] = {}
        for app in params.applications:
            vals = [
                float(r.get('net_run_s'))
                for r in timing_records
                if r.get('application') == app and isinstance(r.get('net_run_s'), (int, float))
            ]
            vals_sorted = sorted(vals)
            timing_by_app[app] = {
                'count': int(len(vals_sorted)),
                'mean_s': float(sum(vals_sorted) / len(vals_sorted)) if vals_sorted else None,
                'p50_s': _safe_percentile(vals_sorted, 0.50),
                'p95_s': _safe_percentile(vals_sorted, 0.95),
                'max_s': float(vals_sorted[-1]) if vals_sorted else None,
            }

    # Calculate GA-adjusted counts if using GA search
    ga_actual_tasks = None
    if args.search == 'ga':
        search_space_yaml = _load_yaml(args.searchspace)
        ga_cfg = search_space_yaml.get('dse', {}).get('ga', {}) if isinstance(search_space_yaml, dict) else {}
        pop_size = ga_cfg.get('pop_size', 10)
        generations = ga_cfg.get('generations', 5)
        # GA evaluates: pop_size × generations per DSE invocation
        # Exclude processor DSE invocations (they don't use GA)
        non_processor_invocations = dse_invocations - len(params.applications)
        ga_actual_tasks = non_processor_invocations * pop_size * generations

    out: Dict[str, object] = {
        "tierx": os.path.abspath(args.tierx),
        "searchspace": os.path.abspath(args.searchspace),
        "search_strategy": args.search,
        "parameters": {
            "applications": list(params.applications),
            "optimize_metrics": list(params.optimize_metrics),
            "sweep_types": list(params.sweep_types),
            "communication_methods": list(params.communication_methods),
            "power_sources": list(params.power_sources),
            "node_placements": list(params.node_placements),
        },
        "counts": {
            "dse_invocations": int(dse_invocations),
            "workload_strings_total": int(workload_total),
            "workload_strings_skipped": int(workload_skipped),
            "processor_workload_yamls_requested": int(processor_generations_requested),
            "processor_workload_yamls_skipped": int(processor_generations_skipped),
            "requested_tasks_total": int(requested_tasks_total),
            "unique_tasks_with_metric": int(len(unique_tasks_with_metric)),
            "unique_tasks_config_only": int(len(unique_tasks_config_only)),
            "ga_actual_tasks": int(ga_actual_tasks) if ga_actual_tasks is not None else None,
        },
        "by_application": {
            app: {
                'requested_tasks_total': int(requested_tasks_by_app.get(app, 0)),
                'unique_tasks_with_metric': int(len(unique_with_metric_by_app.get(app, set()))),
                'unique_tasks_config_only': int(len(unique_config_only_by_app.get(app, set()))),
            }
            for app in params.applications
        },
        "timing": {
            "records": int(len(timing_records)),
            "summary": timing_summary,
            "by_component_metric": timing_by_group,
            "by_application": timing_by_app if (not args.no_timing and 'timing_by_app' in locals()) else {},
        },
    }

    # Search space knob summary (counts of list options), scoped to relevant sections.
    search_space_yaml = _load_yaml(args.searchspace)
    scoped: Dict[str, Any] = {}
    for k in ('component_generation', 'dse'):
        if isinstance(search_space_yaml.get(k), dict):
            scoped[k] = search_space_yaml.get(k)
    knob_counts = _collect_list_knobs(scoped)
    knob_counts_sorted = sorted(knob_counts, key=lambda x: x[0])
    out['search_space_knobs'] = {k: v for k, v in knob_counts_sorted}

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    c = out["counts"]  # type: ignore[assignment]
    search_strat = out.get("search_strategy", "exhaustive")
    print("")
    print("\033[36m+============================================================================+\033[0m")
    if args.pre_run:
        if search_strat == 'ga':
            print("\033[36m|\033[0m                   DSE STATISTICS (ESTIMATED - GA MODE)                   \033[36m|\033[0m")
        else:
            print("\033[36m|\033[0m                         DSE STATISTICS (ESTIMATED)                        \033[36m|\033[0m")
    else:
        if search_strat == 'ga':
            print("\033[36m|\033[0m                       DSE STATISTICS (GA MODE)                           \033[36m|\033[0m")
        else:
            print("\033[36m|\033[0m                               DSE STATISTICS                               \033[36m|\033[0m")
    print("\033[36m+============================================================================+\033[0m")
    print(f"  Search strategy: {search_strat}")
    print(f"  DSE invocations (DSE.py runs): {c['dse_invocations']}")
    print(f"  Workloads considered (strings): {c['workload_strings_total']} (skipped: {c['workload_strings_skipped']})")
    label_proc = "to generate" if args.pre_run else "generated"
    print(f"  Processor workload YAMLs {label_proc}: {c['processor_workload_yamls_requested']} (skipped: {c['processor_workload_yamls_skipped']})")
    
    if search_strat == 'ga' and c['ga_actual_tasks'] is not None:
        print(f"  Full search space size: {c['requested_tasks_total']}")
        label_tasks = "Estimated GA evaluations" if args.pre_run else "Actual GA evaluations"
        print(f"  {label_tasks}: {c['ga_actual_tasks']} ({c['ga_actual_tasks']/c['requested_tasks_total']*100:.1f}% of search space)")
    else:
        label_tasks = "Estimated simulation tasks" if args.pre_run else "Requested simulation tasks"
        print(f"  {label_tasks} (sum over all sweeps/metrics): {c['requested_tasks_total']}")
    
    print(f"  Unique tasks incl metric (dedup exact repeat): {c['unique_tasks_with_metric']}")
    print(f"  Unique configs excl metric (potential reuse across metrics): {c['unique_tasks_config_only']}")

    by_app = out.get('by_application', {})
    if isinstance(by_app, dict) and by_app:
        print("")
        print("  Breakdown per application")
        for app in params.applications:
            row = by_app.get(app, {})
            if not isinstance(row, dict):
                continue
            print(
                f"    {app}: requested={row.get('requested_tasks_total', 0)}, "
                f"unique(metric)={row.get('unique_tasks_with_metric', 0)}, "
                f"unique(config)={row.get('unique_tasks_config_only', 0)}"
            )

    if not args.no_timing and not args.pre_run:
        t = out.get('timing', {})  # type: ignore[assignment]
        recs = int(t.get('records', 0)) if isinstance(t, dict) else 0
        if recs > 0 and isinstance(t, dict):
            net = (t.get('summary', {}) or {}).get('net_run', {}) if isinstance(t.get('summary', {}), dict) else {}
            ev = (t.get('summary', {}) or {}).get('eval_total', {}) if isinstance(t.get('summary', {}), dict) else {}
            if isinstance(net, dict) and isinstance(ev, dict):
                print("")
                print("  Timing (from data_DSE/timing/*.jsonl)")
                print(f"    Recorded evaluations: {recs}")
                if net.get('mean_s') is not None:
                    print(
                        "    network.run() wall-time: "
                        f"mean={net['mean_s']*1000:.2f}ms, p50={net['p50_s']*1000:.2f}ms, p95={net['p95_s']*1000:.2f}ms, p99={net['p99_s']*1000:.2f}ms, max={net['max_s']*1000:.2f}ms"
                    )
                if ev.get('mean_s') is not None:
                    print(
                        "    Per-eval total (load+setup+run+profiler): "
                        f"mean={ev['mean_s']*1000:.2f}ms, p95={ev['p95_s']*1000:.2f}ms"
                    )

            app_t = t.get('by_application', {})
            if isinstance(app_t, dict) and app_t:
                print("    By application (network.run())")
                for app in params.applications:
                    row = app_t.get(app)
                    if not isinstance(row, dict):
                        continue
                    print(
                        f"      {app}: n={row.get('count', 0)}, "
                        f"mean={_format_ms(row.get('mean_s'))}, p50={_format_ms(row.get('p50_s'))}, p95={_format_ms(row.get('p95_s'))}, max={_format_ms(row.get('max_s'))}"
                    )
        else:
            print("")
            print("  Timing: no records found (run a DSE first, then re-run this summary)")

    knobs = out.get('search_space_knobs', {})
    if isinstance(knobs, dict) and knobs:
        print("")
        print("  Search space knobs (#options)")
        # TierX knobs are the primary run-level combinatorial knobs
        print("    TierX.yaml:")
        print(f"      applications = {len(params.applications)}")
        print(f"      optimize_metrics = {len(params.optimize_metrics)}")
        print(f"      sweep_types = {len(params.sweep_types)}")
        print(f"      communication_methods = {len(params.communication_methods)}")
        print(f"      power_sources = {len(params.power_sources)}")
        print(f"      node_placements = {len(params.node_placements)}")
        print("    SearchSpace.yaml (scoped to component_generation + dse):")
        for k, v in sorted(knobs.items()):
            print(f"      {k} = {v}")

    print("")
    print("  How these numbers are derived")
    print("    - requested_tasks_total = Σ over (app, sweep, metric, workload) [#component_files × #electrodes × #charge_times]")
    print("    - unique_tasks_with_metric = same task key but deduped if identical repeats occur")
    print("    - unique_tasks_config_only = deduped ignoring metric (same config could serve multiple metrics)")
    print("    - workloads_skipped uses the same DSE rule: skip if workload contains both 'BCC' and 'EXTERNAL'")
    print("\033[36m+============================================================================+\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
