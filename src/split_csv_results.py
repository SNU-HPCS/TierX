#!/usr/bin/env python3
"""
Script to split combined CSV files in the results folder.
Each combined CSV file contains data for two sweep options that need to be separated.
The separated files are written to results/separated/ directory.
"""

import os
import re
import glob

def split_csv_file(filepath):
    """
    Split a CSV file containing two sweep options into separate files.
    The separated files are written to results/separated/ directory.
    
    Args:
        filepath: Path to the original combined CSV file
    """
    print(f"Processing: {filepath}")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Split content by looking for lines that start with a sweep type (like "Communication Type:")
    # This regex finds lines that contain "Type:" which indicate the start of a new section
    sections = re.split(r'\n(?=\w+\s+Type:\s+)', content.strip())
    
    if len(sections) < 2:
        print(f"  Warning: File {filepath} doesn't contain two sections. Skipping.")
        return
    
    # Extract the base filename without extension
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    results_dir = os.path.dirname(filepath)

    # Parse filename from the end to identify sweep type and options
    # Example: SpikeSorting_optimize_power_sweeping_node_placement_NECK-ARM_NECK-EXTERNAL_TEMPLE-ARM_LOW-RF_SMALL-CAP
    parts = base_name.split('_')
    # Find the sweep type (e.g., node_placement, communication, power)
    sweep_type_idx = None
    for idx in range(len(parts)):
        if parts[idx].endswith('placement') or parts[idx] in ['communication', 'power']:
            sweep_type_idx = idx
    if sweep_type_idx is None:
        print(f"  Warning: Could not find sweep type in filename: {base_name}")
        return

    prefix = '_'.join(parts[:sweep_type_idx])  # everything before sweep type
    sweep_type = parts[sweep_type_idx]          # sweep type itself
    # The rest are the sweep options, which may be for 2 categories
    options = '_'.join(parts[sweep_type_idx+1:])
    # Split options into two groups: node and the rest (power, comm)
    # Assume the first group (node) is separated by underscores, and the last two are always the other categories
    # For example: NECK-ARM_NECK-EXTERNAL_TEMPLE-ARM_LOW-RF_SMALL-CAP
    # node_options = ['NECK-ARM', 'NECK-EXTERNAL', 'TEMPLE-ARM']
    # other_options = 'LOW-RF_SMALL-CAP'
    if '_' in options:
        opt_split = options.split('_')
        # Find where the non-node options start (by looking for a known comm/power type)
        # We'll assume the last two elements are always the other category options
        node_options = opt_split[:-2]
        other_options = '_'.join(opt_split[-2:])
    else:
        node_options = [options]
        other_options = ''

    # For each section, create a file with comm/power/node order in the name
    # Define known types for matching
    comm_types = ['HIGH-BCC', 'LOW-BCC', 'LOW-RF']
    power_types = ['OFF', 'SMALL-CAP', 'BAT', 'SMALL-BAT']
    node_types = ['NECK-ARM', 'NECK-EXTERNAL', 'TEMPLE-ARM']

    # Parse options into comm, power, node
    def parse_options(opt_str, sweep_option):
        opts = opt_str.split('_')
        comm = None
        power = None
        node = None
        # Find comm
        for o in opts + [sweep_option]:
            if o in comm_types:
                comm = o
        # Find power
        for o in opts + [sweep_option]:
            if o in power_types:
                power = o
        # Find node
        for o in opts + [sweep_option]:
            if o in node_types:
                node = o
        return comm, power, node

    for i, section in enumerate(sections):
        if not section.strip():
            continue

        lines = section.strip().split('\n')
        if not lines:
            continue

        first_line = lines[0]
        match = re.search(r'Type:\s+([\w\-]+)', first_line)
        if not match:
            print(f"  Warning: Could not extract sweep option from line: {first_line}")
            continue

        sweep_option = match.group(1)

        comm, power, node = parse_options(options, sweep_option)
        # Compose new filename: <prefix>_power_COMM_POWER_NODE
        new_name = f"{prefix}_{sweep_type}"
        if comm:
            new_name += f"_{comm}"
        if power:
            new_name += f"_{power}"
        if node:
            new_name += f"_{node}"

        separated_dir = os.path.join(os.path.dirname(results_dir), "results", "separated")
        os.makedirs(separated_dir, exist_ok=True)

        new_filepath = os.path.join(separated_dir, f"{new_name}.csv")

        with open(new_filepath, 'w') as f:
            f.write(section.strip() + '\n')

        print(f"  Created: {new_filepath}")

def main():
    """Main function to process all CSV files in results directory."""
    results_dir = "../results"
    
    if not os.path.exists(results_dir):
        print(f"Results directory '{results_dir}' not found!")
        return
    
    # Find all CSV files that contain combined sweep options (1, 2, or 3) for communication, power, or node
    # Use regex to match any filename with two or more sweep options separated by underscores
    sweep_regex = re.compile(r'_(HIGH-BCC(_LOW-BCC)?(_LOW-RF)?|LOW-BCC(_HIGH-BCC)?(_LOW-RF)?|LOW-RF(_HIGH-BCC)?(_LOW-BCC)?|OFF(_SMALL-CAP)?(_BAT)?(_SMALL-BAT)?|SMALL-CAP(_OFF)?(_BAT)?(_SMALL-BAT)?|BAT(_OFF)?(_SMALL-CAP)?(_SMALL-BAT)?|SMALL-BAT(_OFF)?(_SMALL-CAP)?(_BAT)?|NECK-ARM(_NECK-EXTERNAL)?(_TEMPLE-ARM)?|NECK-EXTERNAL(_NECK-ARM)?(_TEMPLE-ARM)?|TEMPLE-ARM(_NECK-ARM)?(_NECK-EXTERNAL)?)_')
    csv_files = []
    for root, dirs, files in os.walk(results_dir):
        for file in files:
            if file.endswith('.csv') and sweep_regex.search(file):
                csv_files.append(os.path.join(root, file))
    
    if not csv_files:
        print("No combined CSV files found to split.")
        return
    
    print(f"Found {len(csv_files)} combined CSV files to split:")
    
    for csv_file in csv_files:
        split_csv_file(csv_file)
    
    print(f"\nProcessing complete! Separated files written to ../results/separated/")

if __name__ == "__main__":
    main()
