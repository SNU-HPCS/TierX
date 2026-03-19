#!/usr/bin/env python3
"""
Summarize best solutions found across all search strategies (exhaustive, pruning, GA).
Aggregates results by application and optimization metric.
"""

import argparse
import glob
import json
import os
from collections import defaultdict


def load_best_solutions(data_dir='data_DSE'):
    """Load all best solution JSON files from the data directory and subdirectories."""
    solutions = []
    
    # Search in main directory
    pattern = os.path.join(data_dir, '*_best_solution_*.json')
    files = glob.glob(pattern)
    
    # Also search in subdirectories (exhaustive/, ga/, pruning/)
    for subdir in ['exhaustive', 'ga', 'pruning']:
        subdir_path = os.path.join(data_dir, subdir)
        if os.path.exists(subdir_path):
            pattern = os.path.join(subdir_path, '*_best_solution_*.json')
            files.extend(glob.glob(pattern))
    
    for filepath in sorted(files):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                data['_source_file'] = os.path.basename(filepath)
                solutions.append(data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load {filepath}: {e}")
    
    return solutions


def extract_app_from_workload(workload_or_file):
    """Extract application name from workload string or filename."""
    if workload_or_file is None:
        return 'Unknown'
    
    workload = str(workload_or_file)
    
    # Check for known application names
    for app in ['NN', 'Seizure', 'SpikeSorting', 'GRU']:
        if app in workload:
            return app
    
    return 'Unknown'


def organize_by_app_and_metric(solutions):
    """Organize solutions by (application, metric, sweep_type).
    
    Group by sweep_type as well since GA evaluations are per sweep type.
    """
    organized = defaultdict(list)
    
    for sol in solutions:
        metric = sol.get('metric', 'unknown')
        sweep_type = sol.get('sweep_type', 'unknown')
        
        # Try to get application from various sources
        app = None
        
        # First, check direct 'application' field
        if 'application' in sol:
            app = sol['application']
        
        # From workload field
        if app is None or app == 'Unknown':
            if 'workload' in sol:
                app = extract_app_from_workload(sol['workload'])
        
        # From source filename
        if app is None or app == 'Unknown':
            app = extract_app_from_workload(sol.get('_source_file', ''))
        
        if app and app != 'Unknown':
            # Include sweep_type in the key so we track per-sweep best solutions
            organized[(app, metric, sweep_type)].append(sol)
    
    return organized


def organize_by_strategy(solutions):
    """Organize solutions by (application, metric, sweep_type, search_strategy).
    
    This enables comparison across different search strategies.
    """
    organized = defaultdict(list)
    
    for sol in solutions:
        metric = sol.get('metric', 'unknown')
        sweep_type = sol.get('sweep_type', 'unknown')
        search_strategy = sol.get('search_strategy', 'unknown')
        
        app = sol.get('application')
        if app is None or app == 'Unknown':
            if 'workload' in sol:
                app = extract_app_from_workload(sol['workload'])
        if app is None or app == 'Unknown':
            app = extract_app_from_workload(sol.get('_source_file', ''))
        
        if app and app != 'Unknown':
            organized[(app, metric, sweep_type, search_strategy)].append(sol)
    
    return organized


def find_best_per_strategy(organized):
    """Find the single best solution for each (app, metric, sweep_type, search_strategy) combination."""
    best = {}
    
    for key, solutions in organized.items():
        if not solutions:
            continue
        best_sol = max(solutions, key=lambda s: s.get('fitness', float('-inf')))
        best[key] = best_sol
    
    return best


def find_best_per_category(organized):
    """Find the single best solution for each (app, metric, sweep_type) combination."""
    best = {}
    
    for (app, metric, sweep_type), solutions in organized.items():
        if not solutions:
            continue
        
        # Find the best based on fitness (higher is better)
        best_sol = max(solutions, key=lambda s: s.get('fitness', float('-inf')))
        best[(app, metric, sweep_type)] = best_sol
    
    return best


def format_objective(value, unit):
    """Format objective value with appropriate precision."""
    if value is None:
        return 'N/A'
    if isinstance(value, float):
        return f"{value:.4f} {unit}"
    return f"{value} {unit}"


def print_summary(best_solutions, output_format='table'):
    """Print summary of best solutions per (application, metric, sweep_type)."""
    
    if not best_solutions:
        print("\nNo best solutions found. Run DSE search first.")
        return
    
    if output_format == 'json':
        # JSON output - organize by app -> metric -> sweep_type
        output = {}
        for (app, metric, sweep_type), sol in sorted(best_solutions.items()):
            if app not in output:
                output[app] = {}
            if metric not in output[app]:
                output[app][metric] = {}
            output[app][metric][sweep_type] = {
                'objective_value': sol.get('objective_value'),
                'objective_unit': sol.get('objective_unit', ''),
                'fitness': sol.get('fitness'),
                'search_strategy': sol.get('search_strategy', 'unknown'),
                'params': sol.get('params', {}),
            }
        print(json.dumps(output, indent=2))
        return
    
    # Table output
    print("\n" + "═" * 90)
    print("       BEST SOLUTIONS BY APPLICATION, METRIC, AND SWEEP TYPE")
    print("═" * 90)
    
    # Get unique applications
    apps = sorted(set(app for app, _, _ in best_solutions.keys()))
    
    for app in apps:
        print(f"\n┌{'─' * 88}┐")
        print(f"│ {'APPLICATION: ' + app:^86} │")
        print(f"├{'─' * 88}┤")
        
        for metric in ['throughput', 'latency', 'operatingtime', 'implant_power']:
            # Find all sweep types for this app + metric combo
            sweep_types_for_metric = sorted(set(
                sweep_type for (a, m, sweep_type) in best_solutions.keys()
                if a == app and m == metric
            ))
            
            if not sweep_types_for_metric:
                continue
            
            for sweep_type in sweep_types_for_metric:
                key = (app, metric, sweep_type)
                if key not in best_solutions:
                    continue
                
                sol = best_solutions[key]
                obj_val = sol.get('objective_value')
                obj_unit = sol.get('objective_unit', '')
                fitness = sol.get('fitness', 0)
                search_strat = sol.get('search_strategy', 'unknown')
                params = sol.get('params', {})
                
                print(f"│ {'Metric: ' + metric + ' | Sweep: ' + sweep_type + ' (' + search_strat + ')':78} │")
                print(f"│   Objective: {format_objective(obj_val, obj_unit):<66} │")
                print(f"│   Fitness:   {fitness:<66.4f} │")
                
                # Show key parameters
                if params:
                    if 'electrodes' in params:
                        print(f"│   Electrodes: {params['electrodes']:<65} │")
                    if 'charge_time' in params:
                        print(f"│   Charge Time: {params['charge_time']:<64} │")
                    if 'component_file' in params:
                        comp = str(params['component_file'])[:55]
                        print(f"│   Component: {comp:<66} │")
                    if 'workload' in params:
                        wl = str(params['workload'])[:55]
                        print(f"│   Workload: {wl:<67} │")
                
                print(f"├{'─' * 88}┤")
        
        print(f"└{'─' * 88}┘")
    
    print("\n" + "═" * 90)


def save_summary(best_solutions, output_file):
    """Save summary to a JSON file, organized by (app, metric, sweep_type)."""
    output = {}
    for (app, metric, sweep_type), sol in sorted(best_solutions.items()):
        if app not in output:
            output[app] = {}
        if metric not in output[app]:
            output[app][metric] = {}
        output[app][metric][sweep_type] = {
            'objective_value': sol.get('objective_value'),
            'objective_unit': sol.get('objective_unit', ''),
            'fitness': sol.get('fitness'),
            'search_strategy': sol.get('search_strategy', 'unknown'),
            'params': sol.get('params', {}),
        }
    
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved summary to: {output_file}")


def print_comparison(best_by_strategy, output_format='table'):
    """Print comparison of best solutions across search strategies."""
    
    if not best_by_strategy:
        print("\nNo best solutions found. Run DSE with multiple search strategies first.")
        return
    
    if output_format == 'json':
        # JSON output organized for comparison
        output = {}
        for (app, metric, sweep_type, strategy), sol in sorted(best_by_strategy.items()):
            key = f"{app}_{metric}_{sweep_type}"
            if key not in output:
                output[key] = {'app': app, 'metric': metric, 'sweep_type': sweep_type, 'strategies': {}}
            output[key]['strategies'][strategy] = {
                'objective_value': sol.get('objective_value'),
                'objective_unit': sol.get('objective_unit', ''),
                'fitness': sol.get('fitness'),
            }
        print(json.dumps(output, indent=2))
        return
    
    # Table output - group by (app, metric, sweep_type)
    by_category = defaultdict(dict)
    for (app, metric, sweep_type, strategy), sol in best_by_strategy.items():
        by_category[(app, metric, sweep_type)][strategy] = sol
    
    print("\n" + "═" * 100)
    print("     COMPARISON OF BEST SOLUTIONS ACROSS SEARCH STRATEGIES")
    print("═" * 100)
    
    apps = sorted(set(app for app, _, _ in by_category.keys()))
    
    for app in apps:
        print(f"\n┌{'─' * 98}┐")
        print(f"│ {'APPLICATION: ' + app:^96} │")
        print(f"├{'─' * 98}┤")
        
        for metric in ['throughput', 'latency', 'operatingtime', 'implant_power']:
            # Get all sweep types for this app + metric
            sweep_types_for_metric = sorted(set(
                sweep_type for (a, m, sweep_type) in by_category.keys()
                if a == app and m == metric
            ))
            
            for sweep_type in sweep_types_for_metric:
                key = (app, metric, sweep_type)
                if key not in by_category:
                    continue
                
                strategies = by_category[key]
                
                print(f"│ Metric: {metric:<15} | Sweep: {sweep_type:<15}{'':48} │")
                print(f"│{'-' * 98}│")
                print(f"│   {'Strategy':<15} {'Objective Value':<25} {'Fitness':<20} {'Winner':>30} │")
                print(f"│{'-' * 98}│")
                
                # Find best fitness among strategies
                best_fitness = max(s.get('fitness', float('-inf')) for s in strategies.values())
                
                for strat in ['exhaustive', 'pruning', 'ga']:
                    if strat in strategies:
                        sol = strategies[strat]
                        obj_val = sol.get('objective_value')
                        obj_unit = sol.get('objective_unit', '')
                        fitness = sol.get('fitness', 0)
                        
                        obj_str = f"{obj_val:.4f} {obj_unit}" if isinstance(obj_val, float) else f"{obj_val} {obj_unit}"
                        is_best = '★ BEST' if fitness == best_fitness else ''
                        
                        print(f"│   {strat:<15} {obj_str:<25} {fitness:<20.4f} {is_best:>30} │")
                    else:
                        print(f"│   {strat:<15} {'(not run)':<25} {'-':<20} {'':>30} │")
                
                print(f"├{'─' * 98}┤")
        
        print(f"└{'─' * 98}┘")
    
    print("\n" + "═" * 100)


def save_comparison(best_by_strategy, output_file):
    """Save comparison to a JSON file."""
    output = {}
    for (app, metric, sweep_type, strategy), sol in sorted(best_by_strategy.items()):
        if app not in output:
            output[app] = {}
        if metric not in output[app]:
            output[app][metric] = {}
        if sweep_type not in output[app][metric]:
            output[app][metric][sweep_type] = {}
        output[app][metric][sweep_type][strategy] = {
            'objective_value': sol.get('objective_value'),
            'objective_unit': sol.get('objective_unit', ''),
            'fitness': sol.get('fitness'),
            'params': sol.get('params', {}),
        }
    
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved comparison to: {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Summarize best solutions across all search strategies by application and metric')
    parser.add_argument('--data-dir', default='data_DSE', help='Directory containing best solution JSON files')
    parser.add_argument('--format', choices=['table', 'json'], default='table', help='Output format')
    parser.add_argument('--save', type=str, help='Save summary to JSON file')
    parser.add_argument('--compare', action='store_true', help='Compare best solutions across search strategies')
    args = parser.parse_args()
    
    # Load all solutions
    solutions = load_best_solutions(args.data_dir)
    print(f"Found {len(solutions)} best solution files")
    
    if not solutions:
        print("No best solution files found. Run DSE with exhaustive, pruning, or GA search first.")
        return
    
    if args.compare:
        # Comparison mode: show results by search strategy
        organized = organize_by_strategy(solutions)
        best = find_best_per_strategy(organized)
        print_comparison(best, args.format)
        if args.save:
            save_comparison(best, args.save)
    else:
        # Default mode: show best across all strategies
        organized = organize_by_app_and_metric(solutions)
        best = find_best_per_category(organized)
        print_summary(best, args.format)
        if args.save:
            save_summary(best, args.save)


if __name__ == '__main__':
    main()
