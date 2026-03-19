#!/usr/bin/env python3
"""
Script to parse and analyze all CSV files in the results folder.
Consolidates results into a single CSV file with analysis.
"""

import os
import re
import csv
import glob
from pathlib import Path

def parse_filename(filename):
    """
    Parse the filename to extract application, metric, sweep type, and options.
    
    Example filename: NN_optimize_throughput_sweeping_operatingtime_CAP_CLOSE_BCC.csv
    Returns: dict with parsed components
    """
    # Remove .csv extension
    name = filename.replace('.csv', '')
    
    # Split by underscores
    parts = name.split('_')
    
    result = {
        'application': None,
        'optimize_metric': None,
        'sweep_type': None,
        'communication_option': None,
        'power_option': None,
        'node_option': None
    }
    
    # Find application (first part)
    if len(parts) > 0:
        result['application'] = parts[0]
    
    # Find optimize metric (after 'optimize')
    try:
        optimize_idx = parts.index('optimize')
        if optimize_idx + 1 < len(parts):
            result['optimize_metric'] = parts[optimize_idx + 1]
    except ValueError:
        pass
    
    # Find sweep type (after 'sweeping')
    try:
        sweeping_idx = parts.index('sweeping')
        if sweeping_idx + 1 < len(parts):
            result['sweep_type'] = parts[sweeping_idx + 1]
    except ValueError:
        pass
    
    # Analyze the remaining parts to identify options
    # Use new comm/power/env types
    comm_types = ['HIGH-BCC', 'LOW-BCC', 'LOW-RF']
    power_types = ['OFF', 'SMALL-CAP', 'BAT', 'SMALL-BAT']
    env_types = ['NECK-ARM', 'NECK-EXTERNAL', 'TEMPLE-ARM']

    for part in parts:
        if part in comm_types:
            result['communication_option'] = part
        elif part in power_types:
            result['power_option'] = part
        elif part in env_types:
            result['node_option'] = part
    
    return result

def parse_csv_content(filepath):
    """
    Parse the content of a CSV file to extract the maximum value and coordinates.
    
    Returns: dict with parsed results
    """
    result = {
        'max_electrode_count': None,
        'min_latency': None,
        'max_operatingtime': None
    }
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Look for max throughput (electrode count)
        max_throughput_match = re.search(r'Max throughput[,:]?\s*([0-9.]+)', content)
        if max_throughput_match:
            result['max_electrode_count'] = float(max_throughput_match.group(1))
        
        # Look for max electrode count (alternative format)
        max_elec_match = re.search(r'Max electrode count[,:]?\s*([0-9.]+)', content)
        if max_elec_match:
            result['max_electrode_count'] = float(max_elec_match.group(1))

        # Look for max latency value (this is 1/latency, so max means min actual latency)
        max_latency_match = re.search(r'Max latency[,:]?\s*([0-9.]+)', content)
        if max_latency_match:
            # Since Plot_graph.py stores 1/latency, we need to convert back
            inv_latency = float(max_latency_match.group(1))
            result['min_latency'] = inv_latency
        
        # Look for max operating time
        max_operatingtime_match = re.search(r'Max operatingtime[,:]?\s*([0-9.]+)', content)
        if max_operatingtime_match:
            operatingtime = float(max_operatingtime_match.group(1))
            result['max_operatingtime'] = operatingtime
            
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
    
    return result

def parse_csv_content_for_coordinates(filepath, target_coords):
    """
    Parse CSV content to extract values at specific coordinates.
    
    Args:
        filepath: Path to the CSV file
        target_coords: List of coordinate tuples to look for, e.g., [(0,0), (7,0), (0,7)]
    
    Returns:
        dict with coordinate values
    """
    result = {}
    
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        # Parse the CSV data to find values at specific coordinates
        # The format is: NR,OR,Value
        data_started = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Look for header line that indicates data section
            if line.startswith('NR,OR,') or line.startswith('NR, OR,'):
                data_started = True
                continue
            
            # Process data lines
            if data_started:
                # Skip lines that are clearly not data (like summary lines)
                if line.startswith('Min ') or line.startswith('Max '):
                    break
                    
                # Parse comma-separated numeric data
                parts = line.split(',')
                if len(parts) == 3:
                    try:
                        nr = float(parts[0].strip())
                        or_val = float(parts[1].strip())
                        value = float(parts[2].strip())
                        
                        # Convert to integer coordinates if they are whole numbers
                        nr_int = int(nr) if nr == int(nr) else nr
                        or_int = int(or_val) if or_val == int(or_val) else or_val
                        
                        coord_tuple = (nr_int, or_int)
                        
                        # Only store if it's one of our target coordinates
                        if coord_tuple in target_coords:
                            result[coord_tuple] = value
                            
                    except (ValueError, AttributeError):
                        continue
    
    except Exception as e:
        print(f"Error parsing coordinates from {filepath}: {e}")
    
    return result

def get_target_coordinates_for_application(application):
    """
    Get target coordinates based on application pipeline length.
    
    Returns: list of coordinate tuples
    """
    # Pipeline lengths from PEs.yaml
    pipeline_lengths = {
        'NN': 7,        # [SBP, BMUL, BMUL, ADD, BMUL, BMUL, BMUL]
        'Seizure': 6,   # [BBF_partial, BBF_partial, BBF_partial, SVM, SVM, THR]
        'SpikeSorting': 4,  # [HCONV, EMDH, GATE, CCHECK]
        'GRU': 8        # [BBF_IM, NEO_IM, THR_IM, BIN_IM, MAD_IM, GRU_IM, GRU_IM, MAD_IM]
    }
    
    if application not in pipeline_lengths:
        print(f"Warning: Unknown application: {application}. Using default pipeline length of 7.")
        total_kernels = 7
    else:
        total_kernels = pipeline_lengths.get(application)
    
    return [(0, 0), (total_kernels - 1, 0), (0, total_kernels - 1)]

def analyze_results():
    """
    Main function to analyze all CSV files and create consolidated report.
    """
    results_dir = "../results/separated"
    
    if not os.path.exists(results_dir):
        print(f"Results directory '{results_dir}' not found!")
        return
    
    # Find all CSV files in the separated directory
    csv_files = glob.glob(os.path.join(results_dir, "*.csv"))
    
    if not csv_files:
        print("No CSV files found in results directory.")
        return
    
    print(f"Found {len(csv_files)} CSV files to analyze...")
    
    # Create analysis directory if it doesn't exist
    analysis_dir = "../results/analysis"
    os.makedirs(analysis_dir, exist_ok=True)
    
    # Consolidated results
    consolidated_results = []
    
    for csv_file in csv_files:
        filename = os.path.basename(csv_file)
        print(f"Processing: {filename}")
        
        # Parse filename
        file_info = parse_filename(filename)
        
        # Parse content
        content_info = parse_csv_content(csv_file)
        
        # Combine information
        result_row = {
            'Application': file_info['application'],
            'optimize_metric': file_info['optimize_metric'],
            'sweep_type': file_info['sweep_type'],
            'communication_option': file_info['communication_option'],
            'power_option': file_info['power_option'],
            'node_option': file_info['node_option'],
            'max_electrode_count': content_info['max_electrode_count'],
            'min_latency': content_info['min_latency'],
            'max_operatingtime': content_info['max_operatingtime'],
        }
        
        consolidated_results.append(result_row)
    
    # Write consolidated results to CSV
    output_file = '../results/analysis/consolidated_results.csv'
    
    fieldnames = [
        'Application',
        'optimize_metric', 
        'sweep_type',
        'communication_option',
        'power_option',
        'node_option',
        'max_electrode_count',
        'min_latency',
        'max_operatingtime',
    ]
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(consolidated_results)
    
    print(f"\nConsolidated results written to: {output_file}")
    print(f"Total records: {len(consolidated_results)}")
    
    # === SINGLE-TIER ANALYSIS ===
    print("\n=== SINGLE-TIER ANALYSIS ===")
    print("Analyzing values at application-specific coordinates...")
    
    single_tier_results = []
    
    for csv_file in csv_files:
        filename = os.path.basename(csv_file)
        
        # Parse filename
        file_info = parse_filename(filename)
        application = file_info['application']
        
        # Get target coordinates for this application
        target_coordinates = get_target_coordinates_for_application(application)
        
        # Parse coordinate data
        coord_data = parse_csv_content_for_coordinates(csv_file, target_coordinates)
        
        # Extract values for each target coordinate (dynamic based on application)
        coord_00_value = coord_data.get((0, 0), None)
        
        # Get the application-specific coordinates
        pipeline_lengths = {
            'NN': 7, 'Seizure': 6, 'SpikeSorting': 4, 'GRU': 8
        }
        total_kernels = pipeline_lengths.get(application, 7)
        
        # coord_n0_value = coord_data.get((total_kernels - 1, 0), None)  # (total_kernels, 0)
        # coord_0n_value = coord_data.get((0, total_kernels - 1), None)  # (0, total_kernels)
        
        coord_n0_value = coord_data.get((total_kernels, 0), None) if total_kernels > 1 else None
        coord_0n_value = coord_data.get((0, total_kernels), None) if total_kernels > 1 else None

        # Get the optimize metric for this file
        optimize_metric = file_info['optimize_metric']
        
        # Create result row with separate columns for each metric-coordinate combination
        single_tier_row = {
            'Application': file_info['application'],
            'optimize_metric': file_info['optimize_metric'],
            'sweep_type': file_info['sweep_type'],
            'communication_option': file_info['communication_option'],
            'power_option': file_info['power_option'],
            'node_option': file_info['node_option'],
            # Throughput columns
            'throughput_implant': coord_00_value if optimize_metric == 'throughput' else None,
            'throughput_near': coord_n0_value if optimize_metric == 'throughput' else None,
            'throughput_off': coord_0n_value if optimize_metric == 'throughput' else None,
            # Latency columns
            'latency_implant': coord_00_value if optimize_metric == 'latency' else None,
            'latency_near': coord_n0_value if optimize_metric == 'latency' else None,
            'latency_off': coord_0n_value if optimize_metric == 'latency' else None,
            # Power columns
            'operatingtime_implant': coord_00_value if optimize_metric == 'operatingtime' else None,
            'operatingtime_near': coord_n0_value if optimize_metric == 'operatingtime' else None,
            'operatingtime_off': coord_0n_value if optimize_metric == 'operatingtime' else None
        }
        
        single_tier_results.append(single_tier_row)
    
    # Write single-tier results to CSV
    single_tier_output = '../results/analysis/single_tier_results.csv'
    
    single_tier_fieldnames = [
        'Application',
        'optimize_metric', 
        'sweep_type',
        'communication_option',
        'power_option',
        'node_option',
        'throughput_implant',
        'throughput_near',
        'throughput_off',
        'latency_implant',
        'latency_near',
        'latency_off',
        'operatingtime_implant',
        'operatingtime_near',
        'operatingtime_off'
    ]
    
    with open(single_tier_output, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=single_tier_fieldnames)
        writer.writeheader()
        writer.writerows(single_tier_results)
    
    print(f"Single-tier results written to: {single_tier_output}")
    print(f"Single-tier records: {len(single_tier_results)}")
    
    # Print summary statistics
    print("\n=== SUMMARY STATISTICS ===")
    
    # Count by application
    apps = {}
    for row in consolidated_results:
        app = row['Application']
        if app:
            apps[app] = apps.get(app, 0) + 1
    
    print(f"\nResults by Application:")
    for app, count in sorted(apps.items()):
        print(f"  {app}: {count} files")
    
    # Count by optimization metric
    metrics = {}
    for row in consolidated_results:
        metric = row['optimize_metric']
        if metric:
            metrics[metric] = metrics.get(metric, 0) + 1
    
    print(f"\nResults by Optimization Metric:")
    for metric, count in sorted(metrics.items()):
        print(f"  {metric}: {count} files")
    
    # Count by sweep type
    sweeps = {}
    for row in consolidated_results:
        sweep = row['sweep_type']
        if sweep:
            sweeps[sweep] = sweeps.get(sweep, 0) + 1
    
    print(f"\nResults by Sweep Type:")
    for sweep, count in sorted(sweeps.items()):
        print(f"  {sweep}: {count} files")
    
    # Find best throughput results
    throughput_results = [r for r in consolidated_results if r['max_electrode_count'] is not None]
    if throughput_results:
        best_throughput = max(throughput_results, key=lambda x: x['max_electrode_count'])
        print(f"\nBest Throughput Result:")
        print(f"  Application: {best_throughput['Application']}")
        print(f"  Max Electrodes: {best_throughput['max_electrode_count']}")
        print(f"  Communication: {best_throughput['communication_option']}")
        print(f"  Power: {best_throughput['power_option']}")
        print(f"  Environment: {best_throughput['node_option']}")
    
    # Find best latency results
    latency_results = [r for r in consolidated_results if r['min_latency'] is not None]
    if latency_results:
        best_latency = max(latency_results, key=lambda x: x['min_latency'])
        print(f"\nBest Latency Result:")
        print(f"  Application: {best_latency['Application']}")
        print(f"  Max Speed: {best_latency['min_latency']} ms")
        print(f"  Communication: {best_latency['communication_option']}")
        print(f"  Power: {best_latency['power_option']}")
        print(f"  Environment: {best_latency['node_option']}")
    
    # Find best operatingtime results
    operatingtime_results = [r for r in consolidated_results if r['max_operatingtime'] is not None]
    if operatingtime_results:
        best_operatingtime = max(operatingtime_results, key=lambda x: x['max_operatingtime'])
        print(f"\nBest Operating Time Result:")
        print(f"  Application: {best_operatingtime['Application']}")
        print(f"  Max Operating Time: {best_operatingtime['max_operatingtime']} hours")
        print(f"  Communication: {best_operatingtime['communication_option']}")
        print(f"  Power: {best_operatingtime['power_option']}")
        print(f"  Environment: {best_operatingtime['node_option']}")

if __name__ == "__main__":
    analyze_results()
