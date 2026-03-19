#!/usr/bin/env python3
"""
PE Analysis Tool
Reads PEs.yaml file and calculates latency, power, and communication bandwidth
for each PE in each application pipeline.
"""

import yaml
import argparse
import matplotlib.pyplot as plt
import numpy as np
import csv
from typing import Dict, List, Tuple, Any


def load_pes_yaml(file_path: str) -> Dict[str, Any]:
    """Load the PEs.yaml configuration file"""
    try:
        with open(file_path, 'r') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Error: File {file_path} not found")
        return {}
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
        return {}


def read_application_strides(yaml_path: str) -> Dict[str, List[int]]:
    """
    Reads the strides for each application in the PEs.yaml file.

    Args:
        yaml_path (str): Path to the PEs.yaml file.

    Returns:
        dict: Dictionary mapping application name to its strides list.
    """
    config = load_pes_yaml(yaml_path)
    app_strides = {}
    
    for app in config.get('Application', []):
        name = app.get('name')
        strides = app.get('strides')
        if name and strides is not None:
            # Convert all strides to integers
            app_strides[name] = [int(s) for s in strides]
    
    return app_strides


def evaluate_dimension(dimension_str: str, num_elec: int = 100) -> int:
    """
    Evaluate dimension string with num_elec substitution
    Examples: 
    - "num_elec" -> 1024
    - "num_elec * 6" -> 6144
    - "256" -> 256
    """
    if isinstance(dimension_str, int):
        return dimension_str
    
    dimension_str = str(dimension_str)
    # Replace num_elec with the actual value
    dimension_str = dimension_str.replace('num_elec', str(num_elec))
    
    try:
        # Safely evaluate the expression
        return eval(dimension_str, {"__builtins__": {}})
    except:
        print(f"Warning: Could not evaluate dimension '{dimension_str}', using 1")
        return 1


def calculate_communication_bandwidth(input_dim: Tuple[int, int], output_dim: Tuple[int, int], 
                                    freq_mhz: float, is_first_pe: bool = False, 
                                    prev_output_bw: float = None, bits_per_sample: int = 32,
                                    input_stride: int = 1, output_stride: int = 1) -> Dict[str, float]:
    """
    Calculate input and output communication bandwidth requirements
    
    Args:
        input_dim: (width, depth) input dimensions
        output_dim: (width, depth) output dimensions  
        freq_mhz: Operating frequency in MHz (not used for bandwidth calculation)
        is_first_pe: True if this is the first PE in the pipeline
        prev_output_bw: Output bandwidth from previous PE (for chained PEs)
        bits_per_sample: Bits per data sample (default 32-bit)
        input_stride: Input stride to divide input bandwidth (default 1)
        output_stride: Output stride to divide output bandwidth (default 1)
    
    Returns:
        Dict with input_bw_mbps and output_bw_mbps
    """
    input_width, input_depth = input_dim
    output_width, output_depth = output_dim
    
    # Always use 30 kHz sampling rate for bandwidth calculations
    sampling_rate_hz = 30000  # 30 kHz sampling rate
    
    # Calculate input bandwidth
    if is_first_pe:
        # First PE: use actual sampling rate (30 kHz) and 16-bit data
        input_samples_per_sec = input_width * sampling_rate_hz
        input_bw_bps = input_samples_per_sec * 16  # 16-bit for first PE
        input_bw_mbps = input_bw_bps / 1e6  # Convert to Mbps
        # Apply input stride
        input_bw_mbps = input_bw_mbps / input_stride
    elif prev_output_bw is not None:
        # Use previous PE's output bandwidth as input bandwidth
        input_bw_mbps = prev_output_bw
        # Apply input stride
        input_bw_mbps = input_bw_mbps / input_stride
    else:
        # Fallback: use sampling rate instead of PE frequency
        input_samples_per_sec = input_width * sampling_rate_hz
        input_bw_bps = input_samples_per_sec * bits_per_sample
        input_bw_mbps = input_bw_bps / 1e6  # Convert to Mbps
        # Apply input stride
        input_bw_mbps = input_bw_mbps / input_stride
    
    # Output bandwidth: always use sampling rate, not PE frequency
    output_samples_per_sec = output_width * sampling_rate_hz
    output_bw_bps = output_samples_per_sec * bits_per_sample
    output_bw_mbps = output_bw_bps / 1e6  # Convert to Mbps
    # Apply output stride
    output_bw_mbps = output_bw_mbps / output_stride
    
    return {
        'input_bw_mbps': input_bw_mbps,
        'output_bw_mbps': output_bw_mbps
    }


def analyze_pe(pe_name: str, pe_config: Dict[str, Any], input_dim: Tuple[int, int], 
               output_dim: Tuple[int, int], num_elec: int = 1024, is_first_pe: bool = False,
               prev_output_bw: float = None, input_stride: int = 1, output_stride: int = 1) -> Dict[str, Any]:
    """
    Analyze a single PE and calculate its metrics
    
    Args:
        pe_name: Name of the PE
        pe_config: PE configuration from YAML
        input_dim: Input dimensions (width, depth)
        output_dim: Output dimensions (width, depth)
        num_elec: Number of electrodes for dynamic power scaling
        is_first_pe: True if this is the first PE in the pipeline
        prev_output_bw: Output bandwidth from previous PE
        input_stride: Input stride for bandwidth calculation
        output_stride: Output stride for bandwidth calculation
    
    Returns:
        Dictionary with calculated metrics
    """
    # Extract PE parameters
    max_freq_mhz = pe_config.get('max_freq_mhz', 1.0)
    latency_ms = pe_config.get('latency_ms', 0.0)
    power = pe_config.get('power', {})
    static_power_uw = power.get('static', 0.0)  # in microwatts
    dynamic_power_uw_per_elec = power.get('dynamic', 0.0)  # in microwatts per electrode
    area_kge = pe_config.get('area_kge', 0.0)
    
    # Convert power to milliwatts and scale dynamic power by input_width
    static_power_mw = static_power_uw / 1000.0  # Convert μW to mW
    input_width, _ = input_dim
    dynamic_power_mw = (dynamic_power_uw_per_elec * input_width) / 1000.0  # Scale by input width and convert to mW
    
    # Calculate total power
    total_power_mw = static_power_mw + dynamic_power_mw
    
    # Calculate communication bandwidth (both input and output)
    comm_bw = calculate_communication_bandwidth(input_dim, output_dim, max_freq_mhz, 
                                              is_first_pe, prev_output_bw, 32,
                                              input_stride, output_stride)
    
    # Calculate throughput
    output_width, _ = output_dim
    throughput_samples_per_sec = output_width * max_freq_mhz * 1e6
    
    return {
        'pe_name': pe_name,
        'num_elec': num_elec,
        'input_dim': input_dim,
        'output_dim': output_dim,
        'max_freq_mhz': max_freq_mhz,
        'latency_ms': latency_ms,
        'static_power_mw': static_power_mw,
        'dynamic_power_mw': dynamic_power_mw,
        'total_power_mw': total_power_mw,
        'area_kge': area_kge,
        'input_bw_mbps': comm_bw['input_bw_mbps'],
        'output_bw_mbps': comm_bw['output_bw_mbps'],
        'throughput_samples_per_sec': throughput_samples_per_sec
    }


def parse_dimensions(dimensions_list: List[str]) -> List[Tuple[str, str]]:
    """
    Parse dimensions list that may be split by YAML comma parsing
    Convert ['(num_elec', '1)', '(256', '1)', ...] to [('num_elec', '1'), ('256', '1'), ...]
    """
    result = []
    i = 0
    
    while i < len(dimensions_list):
        if i + 1 < len(dimensions_list):
            # Remove parentheses and combine pairs
            first = str(dimensions_list[i]).strip('(').strip()
            second = str(dimensions_list[i + 1]).strip(')').strip()
            result.append((first, second))
            i += 2
        else:
            # Handle odd case
            item = str(dimensions_list[i]).strip('()').strip()
            result.append((item, '1'))
            i += 1
    
    return result


def analyze_application(app_config: Dict[str, Any], pes_config: Dict[str, Any], 
                       num_elec: int = 100, mode: str = 'normal') -> List[Dict[str, Any]]:
    """
    Analyze all PEs in an application pipeline
    
    Args:
        app_config: Application configuration from YAML
        pes_config: PEs configuration dictionary
        num_elec: Number of electrodes (default 1024)
        mode: Analysis mode ('normal' or 'accum')
    
    Returns:
        List of PE analysis results
    """
    pipeline = app_config.get('pipeline', [])
    raw_dimensions = app_config.get('dimensions', [])
    raw_strides = app_config.get('strides', [])

    # Parse dimensions properly
    if isinstance(raw_dimensions[0], str):
        # Dimensions were split by YAML parsing
        dimensions = parse_dimensions(raw_dimensions)
    else:
        # Dimensions are already tuples/lists
        dimensions = [(str(d[0]), str(d[1])) for d in raw_dimensions]

    # Parse strides properly - they should be integers
    strides = []
    if raw_strides:
        strides = [int(s) for s in raw_strides]
    else:
        # Default to stride of 1 for all PEs if not specified
        strides = [1] * len(pipeline)
    
    if len(pipeline) != len(dimensions):
        print(f"Warning: Pipeline length ({len(pipeline)}) doesn't match dimensions length ({len(dimensions)})")
        return []
    
    if len(pipeline) != len(strides):
        print(f"Warning: Pipeline length ({len(pipeline)}) doesn't match strides length ({len(strides)})")
        # Pad with 1s if strides list is shorter
        strides.extend([1] * (len(pipeline) - len(strides)))
    
    results = []
    prev_output_bw = None  # Track previous PE's output bandwidth
    
    # For accumulation mode, track cumulative values
    cumulative_power = 0.0
    cumulative_latency = 0.0
    cumulative_area = 0.0
    
    for i, (pe_name, dim_tuple) in enumerate(zip(pipeline, dimensions)):
        if pe_name not in pes_config:
            print(f"Warning: PE '{pe_name}' not found in PEs configuration")
            continue
        
        # Get current stride
        current_stride = strides[i] if i < len(strides) else 1
        
        # Parse dimensions - these are OUTPUT dimensions for this PE
        output_width = evaluate_dimension(dim_tuple[0], num_elec)
        output_depth = evaluate_dimension(dim_tuple[1], num_elec)
        output_dim = (output_width, output_depth)
        
        # For input dimensions, use the previous stage's output (or initial input for first stage)
        if i == 0:
            # First stage - input is the original electrode count
            input_dim = (num_elec, 1)
            is_first_pe = True
            input_stride = 1
            output_stride = current_stride
        else:
            # Use previous stage's output as this stage's input
            prev_width = evaluate_dimension(dimensions[i - 1][0], num_elec)
            prev_depth = evaluate_dimension(dimensions[i - 1][1], num_elec)
            input_dim = (prev_width, prev_depth)
            is_first_pe = False
            # For input stride, use previous PE's output stride
            input_stride = strides[i - 1] if i - 1 < len(strides) else 1
            output_stride = current_stride
        
        pe_config = pes_config[pe_name]
        analysis = analyze_pe(pe_name, pe_config, input_dim, output_dim, num_elec, 
                             is_first_pe, prev_output_bw, input_stride, output_stride)
        
        # Apply accumulation mode if requested
        if mode == 'accum':
            # Add current PE's individual metrics to cumulative totals
            cumulative_power += analysis['total_power_mw']
            cumulative_latency += analysis['latency_ms']
            cumulative_area += analysis['area_kge']
            
            # Update the analysis with cumulative values
            analysis['individual_power_mw'] = analysis['total_power_mw']
            analysis['individual_latency_ms'] = analysis['latency_ms']
            analysis['individual_area_kge'] = analysis['area_kge']
            
            analysis['total_power_mw'] = cumulative_power
            analysis['latency_ms'] = cumulative_latency
            analysis['area_kge'] = cumulative_area
        
        results.append(analysis)
        
        # Store this PE's output bandwidth for the next PE
        prev_output_bw = analysis['output_bw_mbps']
    
    return results


def print_analysis_results(app_name: str, results: List[Dict[str, Any]]):
    """Print formatted analysis results for an application"""
    print(f"\n{'='*80}")
    print(f"APPLICATION: {app_name.upper()}")
    print(f"{'='*80}")
    
    if not results:
        print("No results to display")
        return
    
    # Check if this is accumulation mode
    is_accum_mode = 'individual_power_mw' in results[0]
    
    if is_accum_mode:
        # Print header for accumulation mode
        print(f"{'PE Name':<15} {'Freq':<8} {'Cum.Latency':<12} {'Cum.Power':<10} {'Input':<10} {'Output':<10} {'Throughput':<12} {'Cum.Area':<10}")
        print(f"{'(MHz)':<15} {'':<8} {'(ms)':<12} {'(mW)':<10} {'BW(Mbps)':<10} {'BW(Mbps)':<10} {'(Samp/s)':<12} {'(KGE)':<10}")
        print("-" * 115)
    else:
        # Print header for normal mode
        print(f"{'PE Name':<15} {'Freq':<8} {'Latency':<10} {'Static':<8} {'Dynamic':<9} {'Total':<8} "
              f"{'Input':<10} {'Output':<10} {'Throughput':<12} {'Area':<8}")
        print(f"{'(MHz)':<15} {'':<8} {'(ms)':<10} {'(mW)':<8} {'(mW)':<9} {'(mW)':<8} "
              f"{'BW(Mbps)':<10} {'BW(Mbps)':<10} {'(Samp/s)':<12} {'(KGE)':<8}")
        print("-" * 110)
    
    total_power = 0.0
    total_area = 0.0
    total_latency = 0.0
    
    for result in results:
        if is_accum_mode:
            print(f"{result['pe_name']:<15} "
                  f"{result['max_freq_mhz']:<8.2f} "
                  f"{result['latency_ms']:<12.6f} "
                  f"{result['total_power_mw']:<10.2f} "
                  f"{result['input_bw_mbps']:<10.1f} "
                  f"{result['output_bw_mbps']:<10.1f} "
                  f"{result['throughput_samples_per_sec']:<12.2e} "
                  f"{result['area_kge']:<10.1f}")
        else:
            print(f"{result['pe_name']:<15} "
                  f"{result['max_freq_mhz']:<8.2f} "
                  f"{result['latency_ms']:<10.6f} "
                  f"{result['static_power_mw']:<8.2f} "
                  f"{result['dynamic_power_mw']:<9.2f} "
                  f"{result['total_power_mw']:<8.2f} "
                  f"{result['input_bw_mbps']:<10.1f} "
                  f"{result['output_bw_mbps']:<10.1f} "
                  f"{result['throughput_samples_per_sec']:<12.2e} "
                  f"{result['area_kge']:<8.1f}")
        
        # For totals, use individual values if in accum mode, otherwise use displayed values
        if is_accum_mode:
            total_power += result['individual_power_mw']
            total_area += result['individual_area_kge']
            total_latency += result['individual_latency_ms']
        else:
            total_power += result['total_power_mw']
            total_area += result['area_kge']
            total_latency += result['latency_ms']
    
    if is_accum_mode:
        print("-" * 115)
        print(f"{'TOTALS':<15} {'':<8} {total_latency:<12.6f} {total_power:<10.2f} "
              f"{'':<10} {'':<10} {'':<12} {total_area:<10.1f}")
    else:
        print("-" * 110)
        print(f"{'TOTALS':<15} {'':<8} {total_latency:<10.6f} {'':<8} {'':<9} {total_power:<8.2f} "
              f"{'':<10} {'':<10} {'':<12} {total_area:<8.1f}")
    
    # Print pipeline summary
    print(f"\nPipeline Summary:")
    print(f"  Number of PEs: {len(results)}")
    print(f"  Total Power Consumption: {total_power:.2f} mW")
    print(f"  Total Latency: {total_latency:.6f} ms")
    print(f"  Total Area: {total_area:.1f} KGE")
    
    if is_accum_mode:
        final_result = results[-1]
        print(f"\nCumulative Summary (up to final PE):")
        print(f"  Final Cumulative Power: {final_result['total_power_mw']:.2f} mW")
        print(f"  Final Cumulative Latency: {final_result['latency_ms']:.6f} ms")
        print(f"  Final Cumulative Area: {final_result['area_kge']:.1f} KGE")
    
    # Print detailed dimensions
    print(f"\nDetailed Dimensions:")
    for result in results:
        input_w, input_d = result['input_dim']
        output_w, output_d = result['output_dim']
        print(f"  {result['pe_name']:<15}: Input({input_w:>6}, {input_d:>2}) -> Output({output_w:>6}, {output_d:>2})")


def plot_pe_analysis(all_results: Dict[str, List[Dict[str, Any]]], output_file: str = "pe_analysis_plot.png", 
                    excluded_pes: List[str] = None, x_axis: str = 'power-delay', log_scale: str = 'auto', 
                    show_pe_names: bool = False):
    """
    Plot PEs as points with Input BW vs selected X-axis metric
    
    Args:
        all_results: Dictionary mapping application names to their PE analysis results
        output_file: Output PNG file name
        excluded_pes: List of PE names to exclude from the plot
        x_axis: X-axis metric ('power', 'delay', 'power-delay', 'energy-delay')
        log_scale: Log scale option ('auto', 'x', 'y', 'both', 'none')
        show_pe_names: Whether to show PE names as annotations next to points
    """
    if excluded_pes is None:
        excluded_pes = []
    
    plt.figure(figsize=(12, 8))
    
    colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown', 'pink', 'gray']
    markers = ['o', 's', '^', 'D', 'v', '<', '>', 'p']
    
    app_names = list(all_results.keys())
    total_plotted = 0
    total_excluded = 0
    
    def calculate_x_value(result: Dict[str, Any], x_metric: str) -> float:
        """Calculate X-axis value based on selected metric"""
        if x_metric == 'power':
            return result['total_power_mw']
        elif x_metric == 'delay':
            return result['latency_ms']
        elif x_metric == 'power-delay':
            return result['total_power_mw'] * result['latency_ms']
        elif x_metric == 'energy-delay':
            # Energy-Delay Product = Power * Latency^2 (since Energy = Power * Time)
            return result['total_power_mw'] * (result['latency_ms'] ** 2)
        else:
            return result['total_power_mw'] * result['latency_ms']  # default to power-delay
    
    def get_x_label_and_unit(x_metric: str) -> tuple:
        """Get appropriate label and unit for X-axis"""
        if x_metric == 'power':
            return 'Total Power (mW)', 'mW'
        elif x_metric == 'delay':
            return 'Latency (ms)', 'ms'
        elif x_metric == 'power-delay':
            return 'Power-Delay Product (mW·ms)', 'mW·ms'
        elif x_metric == 'energy-delay':
            return 'Energy-Delay Product (mW·ms²)', 'mW·ms²'
        else:
            return 'Power-Delay Product (mW·ms)', 'mW·ms'
    
    x_label, x_unit = get_x_label_and_unit(x_axis)
    
    for i, (app_name, results) in enumerate(all_results.items()):
        if not results:
            continue
            
        x_values = []  # Selected X-axis metric
        y_values = []  # Input BW
        pe_names = []
        
        for result in results:
            pe_name = result['pe_name']
            
            # Skip excluded PEs
            if pe_name in excluded_pes:
                total_excluded += 1
                continue
                
            x_value = calculate_x_value(result, x_axis)
            input_bw = result['input_bw_mbps']
            
            x_values.append(x_value)
            y_values.append(input_bw)
            pe_names.append(pe_name)
            total_plotted += 1
        
        if not x_values:  # Skip if no PEs to plot for this application
            continue
            
        # Plot points for this application
        color = colors[i % len(colors)]
        marker = markers[i % len(markers)]
        
        plt.scatter(x_values, y_values, c=color, marker=marker, s=100, 
                   label=f"{app_name} ({len(x_values)} PEs)", alpha=0.7)
        
        # Add PE name annotations (only if requested)
        if show_pe_names:
            for x, y, name in zip(x_values, y_values, pe_names):
                plt.annotate(name, (x, y), xytext=(5, 5), textcoords='offset points', 
                            fontsize=8, alpha=0.8)
    
    plt.xlabel(x_label, fontsize=12)
    plt.ylabel('Input Bandwidth (Mbps)', fontsize=12)
    title = f'PE Analysis: Input Bandwidth vs {x_label}'
    if excluded_pes:
        title += f' (Excluding: {", ".join(excluded_pes)})'
    plt.title(title, fontsize=14, fontweight='bold')
    
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    
    # Apply log scale based on user preference
    if log_scale == 'auto':
        # Use log scale if the range is large (automatic behavior)
        if max(plt.gca().get_xlim()) / min([x for x in plt.gca().get_xlim() if x > 0]) > 100:
            plt.xscale('log')
        if max(plt.gca().get_ylim()) / min([y for y in plt.gca().get_ylim() if y > 0]) > 100:
            plt.yscale('log')
    elif log_scale == 'x':
        plt.xscale('log')
    elif log_scale == 'y':
        plt.yscale('log')
    elif log_scale == 'both':
        plt.xscale('log')
        plt.yscale('log')
    elif log_scale == 'none':
        # Keep linear scale (do nothing)
        pass
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"\nPlot saved as: {output_file}")
    
    # Print summary statistics
    print(f"\nPlot Summary:")
    print(f"  Total PEs plotted: {total_plotted}")
    if total_excluded > 0:
        print(f"  Total PEs excluded: {total_excluded}")
        print(f"  Excluded PEs: {', '.join(excluded_pes)}")
    print(f"  Applications: {len(all_results)}")
    
    all_x_values = []
    all_bw = []
    for results in all_results.values():
        for result in results:
            if result['pe_name'] not in excluded_pes:
                all_x_values.append(calculate_x_value(result, x_axis))
                all_bw.append(result['input_bw_mbps'])
    
    if all_x_values and all_bw:
        print(f"  {x_label} range: {min(all_x_values):.6f} to {max(all_x_values):.6f} {x_unit}")
        print(f"  Input Bandwidth range: {min(all_bw):.1f} to {max(all_bw):.1f} Mbps")


def generate_csv_file(all_results: Dict[str, List[Dict[str, Any]]], output_file: str = "pe_analysis_data.csv"):
    """
    Generate CSV file with PE analysis data
    
    Args:
        all_results: Dictionary mapping application names to their PE analysis results
        output_file: Output CSV file name
    """
    fieldnames = [
        'PE_idx',
        'Application',
        'Num_Electrodes',
        'Label',
        'PE_Name', 
        'Power',
        'Latency',
        'Power_Delay_Product',
        'Energy_Delay_Product',
        'Input_Bandwidth_Mbps'
    ]
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for app_name, results in all_results.items():
            # Group results by electrode count to track PE index within each pipeline
            elec_groups = {}
            for result in results:
                num_elec = result.get('num_elec', 'Unknown')
                if num_elec not in elec_groups:
                    elec_groups[num_elec] = []
                elec_groups[num_elec].append(result)
            
            # Process each electrode count group separately
            for num_elec, elec_results in elec_groups.items():
                for pe_idx, result in enumerate(elec_results, 1):  # Start PE index from 1
                    # Create label by concatenating app name and electrode count
                    label = f"{app_name}{num_elec}"
                    
                    power_delay_product = result['total_power_mw'] * result['latency_ms']
                    energy_delay_product = result['total_power_mw'] * (result['latency_ms'] ** 2)
                    
                    row = {
                        'PE_idx': pe_idx,
                        'Application': app_name,
                        'Num_Electrodes': num_elec,
                        'Label': label,
                        'PE_Name': result['pe_name'],
                        'Power': result['total_power_mw'],
                        'Latency': result['latency_ms'],
                        'Power_Delay_Product': power_delay_product,
                        'Energy_Delay_Product': energy_delay_product,
                        'Input_Bandwidth_Mbps': result['input_bw_mbps']
                    }
                    writer.writerow(row)
    
    print(f"CSV data saved as: {output_file}")
    
    # Print summary
    total_pes = sum(len(results) for results in all_results.values())
    print(f"CSV Summary:")
    print(f"  Total PEs exported: {total_pes}")
    print(f"  Applications: {len(all_results)}")
    print(f"  Columns: {len(fieldnames)}")


def select_pes_to_exclude(all_results: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """
    Interactive interface to select PEs to exclude from the plot
    
    Args:
        all_results: Dictionary mapping application names to their PE analysis results
    
    Returns:
        List of PE names to exclude
    """
    # Collect all unique PE names
    all_pe_names = set()
    pe_by_app = {}
    
    for app_name, results in all_results.items():
        pe_by_app[app_name] = []
        for result in results:
            pe_name = result['pe_name']
            all_pe_names.add(pe_name)
            pe_by_app[app_name].append(pe_name)
    
    all_pe_names = sorted(list(all_pe_names))
    
    print(f"\n{'='*60}")
    print("PE SELECTION INTERFACE")
    print(f"{'='*60}")
    print("Available PEs by Application:")
    
    # Display PEs by application
    for app_name, pe_names in pe_by_app.items():
        print(f"\n{app_name}:")
        for i, pe_name in enumerate(pe_names):
            print(f"  {i+1}. {pe_name}")
    
    print(f"\nAll unique PEs ({len(all_pe_names)}):")
    for i, pe_name in enumerate(all_pe_names):
        print(f"  {i+1:2d}. {pe_name}")
    
    print(f"\n{'='*60}")
    print("Selection Options:")
    print("1. Enter PE numbers to exclude (e.g., 1,3,5)")
    print("2. Enter PE names to exclude (e.g., SBP,BMUL)")
    print("3. Press Enter to include all PEs")
    print("4. Type 'help' for more options")
    
    excluded_pes = []
    
    while True:
        try:
            user_input = input("\nYour selection: ").strip()
            
            if not user_input:
                # Empty input - include all PEs
                break
                
            elif user_input.lower() == 'help':
                print("\nHelp:")
                print("- Enter numbers: '1,3,5' to exclude PEs 1, 3, and 5 from the list above")
                print("- Enter names: 'SBP,BMUL' to exclude specific PE types")
                print("- Mix both: '1,BMUL,3' to exclude by number and name")
                print("- Empty input: include all PEs")
                continue
                
            # Parse input
            selections = [s.strip() for s in user_input.split(',')]
            excluded_pes = []
            
            for selection in selections:
                if selection.isdigit():
                    # Number selection
                    idx = int(selection) - 1
                    if 0 <= idx < len(all_pe_names):
                        excluded_pes.append(all_pe_names[idx])
                    else:
                        print(f"Warning: PE number {selection} is out of range")
                else:
                    # Name selection
                    if selection in all_pe_names:
                        excluded_pes.append(selection)
                    else:
                        print(f"Warning: PE '{selection}' not found")
            
            # Remove duplicates and confirm
            excluded_pes = list(set(excluded_pes))
            
            if excluded_pes:
                print(f"\nSelected PEs to exclude: {', '.join(excluded_pes)}")
                confirm = input("Confirm selection? (y/n): ").strip().lower()
                if confirm in ['y', 'yes']:
                    break
                else:
                    excluded_pes = []
                    continue
            else:
                print("No valid PEs selected. Including all PEs.")
                break
                
        except KeyboardInterrupt:
            print("\nOperation cancelled. Including all PEs.")
            excluded_pes = []
            break
        except Exception as e:
            print(f"Error: {e}. Please try again.")
    
    return excluded_pes


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Analyze PEs from YAML configuration')
    parser.add_argument('yaml_file', nargs='?', default='lib/Input/PEs.yaml', 
                       help='Path to PEs.yaml file (default: lib/Input/PEs.yaml)')
    parser.add_argument('--num-elec', type=int, default=1024, 
                       help='Number of electrodes for single analysis (default: 1024)')
    parser.add_argument('--elec-range', type=str, help='Electrode range in format "start:end:step" (e.g., "100:1600:100")')
    parser.add_argument('--elec-exp', type=str, help='Exponential electrode range in format "start:end:factor" (multiplies by factor each step, e.g., "100:1600:2" -> 100,200,400,800,1600 or "100:1600:4" -> 100,400,1600)')
    parser.add_argument('--app', type=str, help='Analyze specific application only')
    parser.add_argument('--exclude-pes', type=str, help='Comma-separated list of PE names to exclude from plot')
    parser.add_argument('--no-interactive', action='store_true', help='Skip interactive PE selection')
    parser.add_argument('--mode', type=str, choices=['normal', 'accum'], default='normal',
                       help='Analysis mode: normal (individual PE metrics) or accum (cumulative metrics)')
    parser.add_argument('--x-axis', type=str, choices=['power', 'delay', 'power-delay', 'energy-delay'], 
                       default='power-delay', help='X-axis metric for plotting (default: power-delay)')
    parser.add_argument('--all-x-axis', action='store_true', 
                       help='Generate plots for all possible X-axis metrics')
    parser.add_argument('--log-scale', type=str, choices=['auto', 'x', 'y', 'both', 'none'], default='auto',
                       help='Log scale option: auto (automatic based on range), x (X-axis only), y (Y-axis only), both (both axes), none (linear scale)')
    parser.add_argument('--show-pe-names', action='store_true', 
                       help='Show PE names as annotations next to each point in the plot')
    
    args = parser.parse_args()
    
    # Parse electrode range if provided
    electrode_numbers = []
    exp_factor = None  # Store the exponential factor for filename generation
    if args.elec_exp:
        # Exponential progression with custom factor
        try:
            parts = args.elec_exp.split(':')
            if len(parts) == 2:
                # Default factor of 2 for backward compatibility
                start, end = map(int, parts)
                exp_factor = 2
            elif len(parts) == 3:
                # Custom factor specified
                start, end, exp_factor = map(int, parts)
            else:
                raise ValueError("Exponential electrode range must be in format 'start:end' or 'start:end:factor'")
            
            if exp_factor <= 1:
                raise ValueError("Factor must be greater than 1")
            
            # Generate exponential sequence: start, start*factor, start*factor^2, ... up to end
            current = start
            while current <= end:
                electrode_numbers.append(current)
                current *= exp_factor
            
            print(f"Exponential electrode sweep (factor {exp_factor}): {electrode_numbers}")
        except ValueError as e:
            print(f"Error parsing exponential electrode range: {e}")
            print("Using default single electrode count")
            electrode_numbers = [args.num_elec]
    elif args.elec_range:
        # Linear progression (existing logic)
        try:
            parts = args.elec_range.split(':')
            if len(parts) != 3:
                raise ValueError("Electrode range must be in format 'start:end:step'")
            start, end, step = map(int, parts)
            electrode_numbers = list(range(start, end + 1, step))
            print(f"Linear electrode sweep: {electrode_numbers}")
        except ValueError as e:
            print(f"Error parsing electrode range: {e}")
            print("Using default single electrode count")
            electrode_numbers = [args.num_elec]
    else:
        electrode_numbers = [args.num_elec]
    
    # Load YAML configuration
    config = load_pes_yaml(args.yaml_file)
    if not config:
        return
    
    applications = config.get('Application', [])
    pes_config = config.get('PEs', {})
    
    if not applications:
        print("No applications found in YAML file")
        return
    
    if not pes_config:
        print("No PEs configuration found in YAML file")
        return
    
    print(f"PE Analysis Results (mode = {args.mode})")
    print(f"Configuration file: {args.yaml_file}")
    if len(electrode_numbers) > 1:
        print(f"Electrode sweep: {min(electrode_numbers)} to {max(electrode_numbers)} (step: {electrode_numbers[1] - electrode_numbers[0]})")
    else:
        print(f"Single electrode count: {electrode_numbers[0]}")
    
    # Store all results for plotting
    all_results = {}
    
    # Analyze applications for each electrode count
    for num_elec in electrode_numbers:
        elec_suffix = f"_{num_elec}elec" if len(electrode_numbers) > 1 else ""
        
        print(f"\n{'='*80}")
        print(f"ANALYSIS FOR {num_elec} ELECTRODES")
        print(f"{'='*80}")
        
        for app in applications:
            app_name = app.get('name', 'Unknown')
            
            # Skip if specific app requested and this isn't it
            if args.app and app_name.lower() != args.app.lower():
                continue
            
            results = analyze_application(app, pes_config, num_elec, args.mode)
            
            # For display purposes, show electrode count in title
            display_app_name = app_name + (f" ({num_elec} electrodes)" if len(electrode_numbers) > 1 else "")
            
            # For storage, always use base app name (no electrode suffix for plotting)
            storage_app_name = app_name
            
            print_analysis_results(display_app_name, results)
            
            # Store results for plotting - combine all electrode counts under same app name
            if results:
                if storage_app_name not in all_results:
                    all_results[storage_app_name] = []
                all_results[storage_app_name].extend(results)
    
    # Create plot and CSV
    if all_results:
        # Determine which PEs to exclude
        excluded_pes = []
        
        if args.exclude_pes:
            # Use command line exclusions
            excluded_pes = [pe.strip() for pe in args.exclude_pes.split(',')]
            print(f"\nCommand line exclusions: {', '.join(excluded_pes)}")
        elif not args.no_interactive:
            # Interactive selection
            excluded_pes = select_pes_to_exclude(all_results)
        
        # Generate filename suffix for electrode sweep
        if len(electrode_numbers) > 1:
            if args.elec_exp and exp_factor is not None:
                # Exponential sweep - show start, end values and factor
                elec_suffix = f"_{min(electrode_numbers)}-{max(electrode_numbers)}elec_exp{exp_factor}"
            else:
                # Linear sweep - show start, end, and step
                elec_suffix = f"_{min(electrode_numbers)}-{max(electrode_numbers)}elec_step{electrode_numbers[1] - electrode_numbers[0]}"
        else:
            elec_suffix = f"_{electrode_numbers[0]}elec"
        
        # Generate plot and CSV
        if args.all_x_axis:
            # Generate plots for all X-axis options
            x_axis_options = ['power', 'delay', 'power-delay', 'energy-delay']
            for x_axis in x_axis_options:
                plot_filename = f"pe_analysis_plot_{x_axis.replace('-', '_')}{elec_suffix}.png"
                plot_pe_analysis(all_results, plot_filename, excluded_pes, x_axis, args.log_scale, args.show_pe_names)
            csv_filename = f"pe_analysis_data{elec_suffix}.csv"
            generate_csv_file(all_results, csv_filename)
        else:
            # Generate single plot with specified X-axis
            plot_filename = f"pe_analysis_plot_{args.x_axis.replace('-', '_')}{elec_suffix}.png"
            plot_pe_analysis(all_results, plot_filename, excluded_pes, args.x_axis, args.log_scale, args.show_pe_names)
            csv_filename = f"pe_analysis_data{elec_suffix}.csv"
            generate_csv_file(all_results, csv_filename)


if __name__ == "__main__":
    main()
