#!/usr/bin/env python3
"""
ESP HLS Report Parser for TierX Integration.

Parses Stratus HLS synthesis reports (cynthHL.log / stratus_hls.log),
scheduler reports, accelerator XML, and Vivado reports (when available)
to extract hardware metrics and update PEs.yaml.

Usage:
    # Parse reports from ESP Docker container (copies from container first):
    python esp_report_parser.py --docker esp_workspace --accel mac_stratus --config BASIC_DMA32

    # Parse reports from a local directory:
    python esp_report_parser.py --report-dir /path/to/hls-work-virtex7/bdw_work/modules/mac/BASIC_DMA32

    # Update PEs.yaml with parsed values:
    python esp_report_parser.py --docker esp_workspace --accel mac_stratus --config BASIC_DMA32 --update-pe MAC
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import yaml


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_stratus_hls_log(log_path: str) -> dict:
    """Parse Stratus HLS cynthHL.log or stratus_hls.log for synthesis metrics.

    Extracts:
        - clock_period_ns: Target clock period in nanoseconds
        - total_luts: Total combinational + sequential LUTs
        - total_mults: Total DSP multiplier blocks
        - register_bits: Total register bits
        - implicit_mux_luts: LUTs used for implicit muxes
        - estimated_ctrl_luts: Estimated control logic LUTs
        - plm_memories: List of {name, words, bits, total_bits}
        - fpga_part: Target FPGA part (if any)
        - tech: Technology name
    """
    result = {
        'clock_period_ns': None,
        'total_luts': 0,
        'total_mults': 0,
        'register_bits': 0,
        'implicit_mux_luts': 0,
        'estimated_ctrl_luts': 0,
        'plm_memories': [],
        'fpga_part': None,
        'tech': None,
    }

    if not os.path.exists(log_path):
        return result

    with open(log_path, 'r', errors='replace') as f:
        content = f.read()

    # Clock period
    m = re.search(r'Using a clock period of\s+([\d.]+)\s*ns', content)
    if m:
        result['clock_period_ns'] = float(m.group(1))
    else:
        m = re.search(r'--clock_period is set to\s+"([\d.]+)"', content)
        if m:
            result['clock_period_ns'] = float(m.group(1))

    # Total LUTs/Mults - take the LAST (top-level) occurrence
    for m in re.finditer(r'Total LUTs/Mults\s+([\d,]+)\s+([\d,]+)', content):
        result['total_luts'] = int(m.group(1).replace(',', ''))
        result['total_mults'] = int(m.group(2).replace(',', ''))

    # Register bits
    m_reg = re.search(r'all register bits\s+([\d,]+)', content)
    if m_reg:
        result['register_bits'] = int(m_reg.group(1).replace(',', ''))

    # Implicit mux LUTs
    m_mux = re.search(r'implicit mux LUTs\s+([\d,]+)', content)
    if m_mux:
        result['implicit_mux_luts'] = int(m_mux.group(1).replace(',', ''))

    # Estimated control LUTs
    m_ctrl = re.search(r'estimated cntrl\s+([\d,]+)', content)
    if m_ctrl:
        result['estimated_ctrl_luts'] = int(m_ctrl.group(1).replace(',', ''))

    # PLM memory arrays
    for m_plm in re.finditer(
        r'Array\s+(\w+),\s+(\d+)\s+words\s+x\s+(\d+)\s+bits\s+\((\d+)\s+total',
        content
    ):
        mem = {
            'name': m_plm.group(1),
            'words': int(m_plm.group(2)),
            'bits': int(m_plm.group(3)),
            'total_bits': int(m_plm.group(4)),
        }
        # Avoid duplicates
        if not any(existing['name'] == mem['name'] for existing in result['plm_memories']):
            result['plm_memories'].append(mem)

    # FPGA part
    m_part = re.search(r'Using FPGA tool \w+ and part\s+(\S+)', content)
    if m_part:
        result['fpga_part'] = m_part.group(1).rstrip('.')

    # Technology
    m_tech = re.search(r'Using\s+(\w+)\(?\w*\)?\s+FPGA Tool', content)
    if m_tech:
        result['tech'] = m_tech.group(1).lower()

    return result


def parse_scheduler_report(rpt_path: str) -> dict:
    """Parse Stratus HLS scheduler.rpt for cycle counts per process.

    Returns:
        dict with process_name -> max_state_id (approximate cycle count).
    """
    result = {}
    if not os.path.exists(rpt_path):
        return result

    with open(rpt_path, 'r', errors='replace') as f:
        lines = f.readlines()

    current_process = None
    max_sid = 0

    for line in lines:
        # Detect process sections
        m_proc = re.search(r'Scheduler report for\s*:\s*(\S+)', line)
        if m_proc:
            if current_process and max_sid > 0:
                result[current_process] = max_sid
            current_process = m_proc.group(1)
            max_sid = 0
            continue

        # Extract state IDs: format like "7 (6:FALSE)" or "10 (9:TRUE)"
        m_sid = re.search(r'\s+(\d+)\s+\(\d+:', line)
        if m_sid:
            sid = int(m_sid.group(1))
            if sid > max_sid:
                max_sid = sid

    # Save last process
    if current_process and max_sid > 0:
        result[current_process] = max_sid

    return result


def parse_accelerator_xml(xml_path: str) -> dict:
    """Parse ESP accelerator XML (e.g. mac.xml) for register/config info.

    Returns:
        dict with name, desc, data_size, device_id, hls_tool, params.
    """
    result = {
        'name': None,
        'desc': None,
        'data_size': None,
        'device_id': None,
        'hls_tool': None,
        'params': [],
    }
    if not os.path.exists(xml_path):
        return result

    tree = ET.parse(xml_path)
    root = tree.getroot()
    accel = root.find('accelerator')
    if accel is not None:
        result['name'] = accel.get('name')
        result['desc'] = accel.get('desc')
        result['data_size'] = int(accel.get('data_size', '4'))
        result['device_id'] = accel.get('device_id')
        result['hls_tool'] = accel.get('hls_tool')
        for param in accel.findall('param'):
            result['params'].append({
                'name': param.get('name'),
                'desc': param.get('desc', ''),
            })

    return result


def parse_vivado_utilization_report(rpt_path: str) -> dict:
    """Parse Vivado utilization report for resource usage.

    Returns:
        dict with lut, ff, bram, dsp counts.
    """
    result = {'lut': 0, 'ff': 0, 'bram': 0, 'dsp': 0}
    if not os.path.exists(rpt_path):
        return result

    with open(rpt_path, 'r', errors='replace') as f:
        content = f.read()

    # Typical Vivado utilization report format:
    # | Slice LUTs    |     1234 |   ...
    # | Slice Registers | 567 | ...
    # | Block RAM Tile  | 12 | ...
    # | DSPs            |  3 | ...
    m = re.search(r'Slice LUTs\s*\|\s*(\d+)', content)
    if m:
        result['lut'] = int(m.group(1))

    m = re.search(r'(?:Slice Registers|Register as Flip Flop)\s*\|\s*(\d+)', content)
    if m:
        result['ff'] = int(m.group(1))

    m = re.search(r'Block RAM Tile\s*\|\s*(\d+)', content)
    if m:
        result['bram'] = int(m.group(1))

    m = re.search(r'DSPs\s*\|\s*(\d+)', content)
    if m:
        result['dsp'] = int(m.group(1))

    return result


def parse_vivado_power_report(rpt_path: str) -> dict:
    """Parse Vivado power report for static/dynamic power.

    Returns:
        dict with dynamic_w, static_w (in Watts).
    """
    result = {'dynamic_w': 0.0, 'static_w': 0.0}
    if not os.path.exists(rpt_path):
        return result

    with open(rpt_path, 'r', errors='replace') as f:
        content = f.read()

    # Typical Vivado power report:
    # | Dynamic (W)  | 0.123 |
    # | Device Static (W)  | 0.456 |
    m = re.search(r'Dynamic\s*\(W\)\s*\|\s*([\d.]+)', content)
    if m:
        result['dynamic_w'] = float(m.group(1))

    m = re.search(r'(?:Device\s+)?Static\s*\(W\)\s*\|\s*([\d.]+)', content)
    if m:
        result['static_w'] = float(m.group(1))

    return result


def parse_vivado_timing_report(rpt_path: str) -> dict:
    """Parse Vivado timing report for slack and achieved frequency.

    Returns:
        dict with wns_ns (worst negative slack), achieved_freq_mhz.
    """
    result = {'wns_ns': None, 'achieved_freq_mhz': None}
    if not os.path.exists(rpt_path):
        return result

    with open(rpt_path, 'r', errors='replace') as f:
        content = f.read()

    # WNS
    m = re.search(r'WNS\s*\(ns\)\s*[-:]*\s*([-\d.]+)', content)
    if m:
        result['wns_ns'] = float(m.group(1))

    # Slack
    m = re.search(r'slack\s*(?:\(MET\)|ns)\s*[-:]*\s*([-\d.]+)', content, re.IGNORECASE)
    if m and result['wns_ns'] is None:
        result['wns_ns'] = float(m.group(1))

    return result


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def copy_from_docker(container: str, src: str, dst: str):
    """Copy a file from a Docker container to the host, resolving symlinks."""
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    # Use 'cat' to resolve symlinks and copy actual content
    cmd = ['docker', 'exec', container, 'cat', src]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    with open(dst, 'wb') as f:
        f.write(result.stdout)


def docker_exec(container: str, command: str) -> str:
    """Execute a command inside a Docker container and return stdout."""
    cmd = ['docker', 'exec', container, 'bash', '-c', command]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def find_report_paths_in_docker(container: str, accel_name: str, config_name: str) -> dict:
    """Find ESP HLS report file paths inside a Docker container.

    Args:
        container: Docker container name
        accel_name: Accelerator name (e.g. 'mac_stratus')
        config_name: HLS config name (e.g. 'BASIC_DMA32')

    Returns:
        dict with report type -> container path
    """
    base = f'/root/esp/accelerators/stratus_hls/{accel_name}/hw'
    accel_short = accel_name.replace('_stratus', '')

    paths = {}

    # Try to find the HLS work directory
    for tech in ['virtex7', 'zynq7000', 'virtexu', 'virtexup']:
        hls_work = f'{base}/hls-work-{tech}'
        module_dir = f'{hls_work}/bdw_work/modules/{accel_short}/{config_name}'

        # Check if directory exists
        check = docker_exec(container, f'test -d {module_dir} && echo yes')
        if check == 'yes':
            paths['stratus_hls_log'] = f'{module_dir}/stratus_hls.log'
            paths['cynthHL_log'] = f'{module_dir}/cynthHL.log'
            paths['scheduler_rpt'] = f'{module_dir}/reports/scheduler.rpt'
            paths['tech'] = tech
            break

    # Accelerator XML
    xml_path = f'{base}/{accel_short}.xml'
    check = docker_exec(container, f'test -f {xml_path} && echo yes')
    if check == 'yes':
        paths['accel_xml'] = xml_path

    # Vivado reports (may not exist if synthesis wasn't run)
    if 'stratus_hls_log' in paths:
        vivado_dir = os.path.dirname(paths['stratus_hls_log']) + '/fpga_work/project_vivado'
        for rpt_type in ['utilization', 'power', 'timing_summary']:
            rpt_file = f'{vivado_dir}/{rpt_type}.rpt'
            check = docker_exec(container, f'test -f {rpt_file} && echo yes')
            if check == 'yes':
                paths[f'vivado_{rpt_type}'] = rpt_file

    return paths


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def collect_esp_metrics(report_dir: str = None,
                        docker_container: str = None,
                        accel_name: str = None,
                        config_name: str = None) -> dict:
    """Collect all ESP HLS metrics from reports.

    Either provide report_dir (local) or docker_container + accel_name + config_name.

    Returns:
        dict with all parsed metrics organized by source.
    """
    metrics = {
        'hls': {},
        'scheduler': {},
        'accelerator': {},
        'vivado_utilization': {},
        'vivado_power': {},
        'vivado_timing': {},
        'derived': {},  # Computed values
    }

    if docker_container:
        # Find and copy reports from Docker
        paths = find_report_paths_in_docker(docker_container, accel_name, config_name)
        if not paths:
            print(f"ERROR: Could not find reports for {accel_name}/{config_name} in {docker_container}")
            return metrics

        # Create temp directory for copied files
        tmp_dir = f'/tmp/esp_reports_{accel_name}_{config_name}'
        os.makedirs(tmp_dir, exist_ok=True)

        local_files = {}
        for key, container_path in paths.items():
            if key == 'tech':
                metrics['hls']['tech'] = paths['tech']
                continue
            local_path = os.path.join(tmp_dir, os.path.basename(container_path))
            try:
                copy_from_docker(docker_container, container_path, local_path)
                local_files[key] = local_path
            except subprocess.CalledProcessError:
                print(f"  Warning: Could not copy {container_path}")

        report_dir = tmp_dir

        # Parse each report
        if 'stratus_hls_log' in local_files:
            metrics['hls'] = parse_stratus_hls_log(local_files['stratus_hls_log'])
        elif 'cynthHL_log' in local_files:
            metrics['hls'] = parse_stratus_hls_log(local_files['cynthHL_log'])

        if 'scheduler_rpt' in local_files:
            metrics['scheduler'] = parse_scheduler_report(local_files['scheduler_rpt'])

        if 'accel_xml' in local_files:
            metrics['accelerator'] = parse_accelerator_xml(local_files['accel_xml'])

        if 'vivado_utilization' in local_files:
            metrics['vivado_utilization'] = parse_vivado_utilization_report(local_files['vivado_utilization'])

        if 'vivado_power' in local_files:
            metrics['vivado_power'] = parse_vivado_power_report(local_files['vivado_power'])

        if 'vivado_timing_summary' in local_files:
            metrics['vivado_timing'] = parse_vivado_timing_report(local_files['vivado_timing_summary'])

    elif report_dir:
        # Parse from local directory
        for fname in os.listdir(report_dir):
            fpath = os.path.join(report_dir, fname)
            if fname in ('stratus_hls.log', 'cynthHL.log') and not metrics['hls']:
                metrics['hls'] = parse_stratus_hls_log(fpath)
            elif fname == 'scheduler.rpt':
                metrics['scheduler'] = parse_scheduler_report(fpath)
            elif fname.endswith('.xml'):
                metrics['accelerator'] = parse_accelerator_xml(fpath)
            elif 'utilization' in fname and fname.endswith('.rpt'):
                metrics['vivado_utilization'] = parse_vivado_utilization_report(fpath)
            elif 'power' in fname and fname.endswith('.rpt'):
                metrics['vivado_power'] = parse_vivado_power_report(fpath)
            elif 'timing' in fname and fname.endswith('.rpt'):
                metrics['vivado_timing'] = parse_vivado_timing_report(fpath)

    # Compute derived metrics
    _compute_derived_metrics(metrics)

    return metrics


def _compute_derived_metrics(metrics: dict):
    """Compute derived values from raw parsed metrics."""
    hls = metrics.get('hls', {})
    vivado_power = metrics.get('vivado_power', {})
    vivado_timing = metrics.get('vivado_timing', {})
    scheduler = metrics.get('scheduler', {})

    derived = {}

    # Frequency
    clock_ns = hls.get('clock_period_ns')
    if clock_ns and clock_ns > 0:
        derived['target_freq_mhz'] = round(1000.0 / clock_ns, 2)

        # If Vivado timing is available, compute achieved frequency
        wns = vivado_timing.get('wns_ns')
        if wns is not None:
            achieved_period = clock_ns - wns  # positive slack means faster
            if achieved_period > 0:
                derived['achieved_freq_mhz'] = round(1000.0 / achieved_period, 2)

    # Area estimate: LUTs → approximate kGE (1 LUT ≈ 4-8 gate equivalents for FPGA)
    total_luts = hls.get('total_luts', 0)
    if total_luts > 0:
        # Conservative estimate: 1 FPGA LUT ≈ 6 gate equivalents
        derived['estimated_area_kge'] = round(total_luts * 6 / 1000, 2)
        derived['total_luts'] = total_luts
        derived['total_dsp_mults'] = hls.get('total_mults', 0)

    # Power from Vivado (convert W → µW for TierX)
    if vivado_power.get('dynamic_w', 0) > 0:
        derived['dynamic_power_uw'] = round(vivado_power['dynamic_w'] * 1e6, 2)
    if vivado_power.get('static_w', 0) > 0:
        derived['static_power_uw'] = round(vivado_power['static_w'] * 1e6, 2)

    # Latency from scheduler (compute_kernel is the main processing pipeline)
    if scheduler:
        # Total cycles = sum of all process cycle counts (rough upper bound for sequential)
        # For pipelined design: max(load_input, compute_kernel, store_output) per batch
        compute_cycles = scheduler.get('compute_kernel', 0)
        load_cycles = scheduler.get('load_input', 0)
        store_cycles = scheduler.get('store_output', 0)

        derived['compute_kernel_cycles'] = compute_cycles
        derived['load_input_cycles'] = load_cycles
        derived['store_output_cycles'] = store_cycles

        # Pipeline throughput: max of the three (ping-pong overlap)
        max_pipeline_cycles = max(compute_cycles, load_cycles, store_cycles)
        derived['pipeline_cycles_per_batch'] = max_pipeline_cycles

        # With clock period, compute latency
        if clock_ns and max_pipeline_cycles > 0:
            derived['pipeline_latency_us'] = round(max_pipeline_cycles * clock_ns / 1000, 4)

    # PLM memory summary
    plm_memories = hls.get('plm_memories', [])
    if plm_memories:
        total_plm_bits = sum(m['total_bits'] for m in plm_memories)
        derived['total_plm_bits'] = total_plm_bits
        derived['total_plm_kb'] = round(total_plm_bits / 8 / 1024, 2)

    metrics['derived'] = derived


def compute_tierx_pe_values(metrics: dict, batching_factor: int = 1, chunk_factor_input: int = 1) -> dict:
    """Convert ESP metrics into TierX PEs.yaml compatible values.

    Args:
        metrics: Output from collect_esp_metrics()
        batching_factor: Number of batches per accelerator invocation
        chunk_factor_input: Number of input elements per PLM chunk

    Returns:
        dict with max_freq_mhz, power.static, power.dynamic, area_kge, latency_ms
    """
    derived = metrics.get('derived', {})
    hls = metrics.get('hls', {})

    pe_values = {}

    # Frequency
    freq = derived.get('achieved_freq_mhz') or derived.get('target_freq_mhz')
    if freq:
        pe_values['max_freq_mhz'] = freq

    # Power (prefer Vivado synthesis values; fall back to estimates)
    power = {}
    if 'static_power_uw' in derived:
        power['static'] = derived['static_power_uw']
    if 'dynamic_power_uw' in derived:
        power['dynamic'] = derived['dynamic_power_uw']
    if power:
        pe_values['power'] = power

    # Area
    if 'estimated_area_kge' in derived:
        pe_values['area_kge'] = derived['estimated_area_kge']

    # Latency: pipeline_latency_us * batching_factor * ceil(total_data / chunk_factor_input)
    if 'pipeline_latency_us' in derived:
        total_latency_us = derived['pipeline_latency_us'] * batching_factor
        pe_values['latency_ms'] = round(total_latency_us / 1000, 6)

    return pe_values


def update_pes_yaml(pe_name: str, pe_values: dict, pes_yaml_path: str = None):
    """Update a specific PE entry in PEs.yaml with ESP-derived values.

    Uses regex-based text replacement to preserve the original file formatting,
    comments, and inline-style lists.  Only updates fields present in pe_values.
    """
    if pes_yaml_path is None:
        pes_yaml_path = os.path.join(
            os.path.dirname(__file__), '..', 'lib', 'Input', 'PEs.yaml'
        )
    pes_yaml_path = os.path.abspath(pes_yaml_path)

    if not os.path.exists(pes_yaml_path):
        print(f"ERROR: PEs.yaml not found at {pes_yaml_path}")
        return False

    with open(pes_yaml_path, 'r') as f:
        content = f.read()

    # Verify the PE exists
    pe_header_pattern = re.compile(rf'^  {re.escape(pe_name)}:\s*$', re.MULTILINE)
    if not pe_header_pattern.search(content):
        print(f"ERROR: PE '{pe_name}' not found in PEs.yaml")
        return False

    # Build per-field replacement patterns scoped to the PE block.
    # A PE block starts with "  PE_NAME:" and ends before the next "  XXXX:" at indent 2.
    pe_block = re.compile(
        rf'(^  {re.escape(pe_name)}:.*?\n)'   # PE header line
        rf'((?:    .*\n|  #.*\n|\n)*)',         # body lines (indent >= 4, comments, blanks)
        re.MULTILINE
    )
    m_block = pe_block.search(content)
    if not m_block:
        print(f"ERROR: Could not locate PE block for '{pe_name}'")
        return False

    block_text = m_block.group(0)
    updated_block = block_text

    # Helper: replace a scalar field value inside the block
    def _replace_field(block: str, field: str, value, indent: int = 4) -> str:
        prefix = ' ' * indent
        pat = re.compile(
            rf'^({prefix}{re.escape(field)}:\s*)(\S.*)$',
            re.MULTILINE
        )
        m = pat.search(block)
        if m:
            # Preserve any inline comment
            old_val_line = m.group(2)
            comment = ''
            comment_match = re.search(r'\s+(#.*)$', old_val_line)
            if comment_match:
                comment = '   ' + comment_match.group(1)
            new_line = f'{m.group(1)}{value}{comment}'
            block = block[:m.start()] + new_line + block[m.end():]
        return block

    if 'max_freq_mhz' in pe_values:
        updated_block = _replace_field(updated_block, 'max_freq_mhz', pe_values['max_freq_mhz'])
        print(f"  Updated max_freq_mhz: {pe_values['max_freq_mhz']}")

    if 'power' in pe_values:
        for k, v in pe_values['power'].items():
            updated_block = _replace_field(updated_block, k, v, indent=6)
            print(f"  Updated power.{k}: {v} µW")

    if 'area_kge' in pe_values:
        updated_block = _replace_field(updated_block, 'area_kge', pe_values['area_kge'])
        print(f"  Updated area_kge: {pe_values['area_kge']}")

    if 'latency_ms' in pe_values:
        updated_block = _replace_field(updated_block, 'latency_ms', pe_values['latency_ms'])
        print(f"  Updated latency_ms: {pe_values['latency_ms']}")

    content = content[:m_block.start()] + updated_block + content[m_block.end():]

    with open(pes_yaml_path, 'w') as f:
        f.write(content)

    print(f"  PEs.yaml updated successfully for PE '{pe_name}'")
    return True


def print_summary(metrics: dict):
    """Print a human-readable summary of parsed ESP metrics."""
    hls = metrics.get('hls', {})
    scheduler = metrics.get('scheduler', {})
    accel = metrics.get('accelerator', {})
    vivado_util = metrics.get('vivado_utilization', {})
    vivado_power = metrics.get('vivado_power', {})
    derived = metrics.get('derived', {})

    print("\n" + "=" * 60)
    print("ESP HLS Synthesis Report Summary")
    print("=" * 60)

    if accel.get('name'):
        print(f"\nAccelerator: {accel['name']} ({accel.get('desc', '')})")
        print(f"  HLS Tool: {accel.get('hls_tool', 'N/A')}")
        print(f"  Data Size: {accel.get('data_size', 'N/A')} bytes")
        if accel.get('params'):
            print(f"  Registers: {', '.join(p['name'] for p in accel['params'])}")

    print(f"\n--- HLS Results (Stratus) ---")
    if hls.get('clock_period_ns'):
        print(f"  Clock Period: {hls['clock_period_ns']} ns ({derived.get('target_freq_mhz', '?')} MHz)")
    if hls.get('fpga_part'):
        print(f"  FPGA Part: {hls['fpga_part']}")
    print(f"  Total LUTs: {hls.get('total_luts', 'N/A')}")
    print(f"  DSP Multipliers: {hls.get('total_mults', 'N/A')}")
    print(f"  Register Bits: {hls.get('register_bits', 'N/A')}")
    print(f"  Implicit Mux LUTs: {hls.get('implicit_mux_luts', 'N/A')}")
    print(f"  Control LUTs: {hls.get('estimated_ctrl_luts', 'N/A')}")

    if hls.get('plm_memories'):
        print(f"\n--- PLM Memories ---")
        for mem in hls['plm_memories']:
            print(f"  {mem['name']}: {mem['words']} x {mem['bits']} bits ({mem['total_bits']} total)")
        if 'total_plm_kb' in derived:
            print(f"  Total PLM: {derived['total_plm_kb']} KB")

    if scheduler:
        print(f"\n--- Scheduler (Cycle Counts) ---")
        for proc, cycles in sorted(scheduler.items()):
            print(f"  {proc}: {cycles} cycles")
        if 'pipeline_latency_us' in derived:
            print(f"  Pipeline Latency: {derived['pipeline_latency_us']} µs (per batch)")

    if any(v > 0 for v in vivado_util.values()):
        print(f"\n--- Vivado Utilization ---")
        print(f"  LUTs: {vivado_util.get('lut', 'N/A')}")
        print(f"  FFs: {vivado_util.get('ff', 'N/A')}")
        print(f"  BRAMs: {vivado_util.get('bram', 'N/A')}")
        print(f"  DSPs: {vivado_util.get('dsp', 'N/A')}")

    if any(v > 0 for v in vivado_power.values()):
        print(f"\n--- Vivado Power ---")
        print(f"  Dynamic: {vivado_power.get('dynamic_w', 'N/A')} W")
        print(f"  Static: {vivado_power.get('static_w', 'N/A')} W")

    print(f"\n--- Derived TierX Values ---")
    if 'estimated_area_kge' in derived:
        print(f"  Estimated Area: {derived['estimated_area_kge']} kGE")
    if derived.get('target_freq_mhz'):
        print(f"  Target Frequency: {derived['target_freq_mhz']} MHz")
    if derived.get('achieved_freq_mhz'):
        print(f"  Achieved Frequency: {derived['achieved_freq_mhz']} MHz")
    if 'dynamic_power_uw' in derived:
        print(f"  Dynamic Power: {derived['dynamic_power_uw']} µW")
    if 'static_power_uw' in derived:
        print(f"  Static Power: {derived['static_power_uw']} µW")

    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Parse ESP HLS synthesis reports and update TierX PEs.yaml'
    )
    parser.add_argument('--docker', type=str, default=None,
                        help='Docker container name (e.g. esp_workspace)')
    parser.add_argument('--accel', type=str, default=None,
                        help='ESP accelerator name (e.g. mac_stratus)')
    parser.add_argument('--config', type=str, default='BASIC_DMA32',
                        help='HLS config name (default: BASIC_DMA32)')
    parser.add_argument('--report-dir', type=str, default=None,
                        help='Local directory containing report files')
    parser.add_argument('--update-pe', type=str, default=None,
                        help='PE name in PEs.yaml to update (e.g. MAC)')
    parser.add_argument('--pes-yaml', type=str, default=None,
                        help='Path to PEs.yaml (default: auto-detect)')
    parser.add_argument('--batching-factor', type=int, default=1,
                        help='Batching factor for latency computation')
    parser.add_argument('--chunk-factor-input', type=int, default=1,
                        help='Chunk factor (input) for latency computation')
    parser.add_argument('--json', action='store_true',
                        help='Output metrics as JSON to stdout')
    parser.add_argument('--json-out', type=str, default=None,
                        help='Export metrics to a JSON file (the ESP→TierX interface file)')

    args = parser.parse_args()

    if not args.docker and not args.report_dir:
        parser.error('Either --docker or --report-dir must be specified')

    if args.docker and not args.accel:
        parser.error('--accel is required when using --docker')

    # Collect metrics
    metrics = collect_esp_metrics(
        report_dir=args.report_dir,
        docker_container=args.docker,
        accel_name=args.accel,
        config_name=args.config,
    )

    if args.json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        print_summary(metrics)

    # Export to JSON interface file if requested
    if args.json_out:
        pe_values = compute_tierx_pe_values(
            metrics,
            batching_factor=args.batching_factor,
            chunk_factor_input=args.chunk_factor_input,
        )
        export_data = {
            'pe_values': pe_values,
            'raw_metrics': metrics,
        }
        out_path = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"\nExported ESP metrics to: {out_path}")
        print("  (Share this file — no Vivado/Cadence license needed to consume it)")

    # Update PEs.yaml if requested
    if args.update_pe:
        pe_values = compute_tierx_pe_values(
            metrics,
            batching_factor=args.batching_factor,
            chunk_factor_input=args.chunk_factor_input,
        )
        print(f"\nComputed TierX PE values for '{args.update_pe}':")
        for k, v in pe_values.items():
            print(f"  {k}: {v}")

        if pe_values:
            print(f"\nUpdating PEs.yaml...")
            update_pes_yaml(args.update_pe, pe_values, args.pes_yaml)
        else:
            print("\nNo values to update (Vivado synthesis reports may be needed for power values)")


if __name__ == '__main__':
    main()
