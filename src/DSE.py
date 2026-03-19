"""
Python script for DSE (Design Space Exploration) in TierX.
This script integrates the baseline Input yaml file with various hardware configurations and simulates the network.
Sweeps through number of electrodes.
"""

import argparse
import copy
import json
import os
import random
import sys
import time
import yaml
from contextlib import redirect_stdout, redirect_stderr
from multiprocessing import Pool, cpu_count

from tqdm import tqdm


def load_search_space(path: str = None):
    """Load search space config; fall back to defaults if missing.
    
    Respects SEARCH_SPACE_CONFIG environment variable if set.
    """
    if path is None:
        # Check environment variable first (set by run.sh)
        path = os.environ.get('SEARCH_SPACE_CONFIG')
        if not path:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            path = os.path.join(base_dir, 'SearchSpace.yaml')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def load_tierx_config(path: str = None):
    """Load TierX.yaml config; fall back to empty dict if missing.
    
    Respects TIERX_CONFIG environment variable if set.
    """
    if path is None:
        path = os.environ.get('TIERX_CONFIG')
        if not path:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            path = os.path.join(base_dir, 'TierX.yaml')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


search_space = load_search_space()
tierx_config = load_tierx_config()

creating_workloads = True
dump_yaml = True
save_graph = True

COMPONENT_TYPES = ['trx', 'processor', 'power', 'env']
APPLICATION_TYPES = ['NN', 'Seizure', 'SpikeSorting', 'GRU']
TRX_LIKE = {'trx', 'power', 'env'}


def _load_pe_config():
    """Load PEs.yaml and parse application-specific configurations.
    
    Returns:
        dict: Application name -> {'dimensions': [...], 'strides': [...], 'sram_accesses': [...]}
    """
    pe_yaml_path = 'lib/Input/PEs.yaml'
    if not os.path.exists(pe_yaml_path):
        return {}
    
    with open(pe_yaml_path, 'r') as f:
        pe_data = yaml.safe_load(f)
    
    config = {}
    for app in pe_data.get('Application', []):
        name = app.get('name')
        if name:
            config[name] = {
                'dimensions': app.get('dimensions', []),
                'strides': app.get('strides', []),
                'scaling': app.get('scaling', []),
                'sram_accesses': app.get('sram_accesses', []),
            }
    return config


def _parse_dimensions(dim_list, num_elec):
    """Parse dimension expressions from PEs.yaml, substituting num_elec.
    
    Args:
        dim_list: List of dimension strings like '(num_elec,1)', '(256,1)', '(num_elec*6,1)'
        num_elec: Number of electrodes to substitute
    
    Returns:
        List of [spatial, temporal] lists
    """
    result = []
    for dim in dim_list:
        if isinstance(dim, str):
            # Parse string like '(num_elec,1)' or '(256,1)'
            try:
                parsed = eval(dim.replace('num_elec', str(num_elec)))
                if isinstance(parsed, tuple):
                    result.append(list(parsed))
                else:
                    result.append([parsed, 1])
            except:
                result.append([num_elec, 1])
        elif isinstance(dim, (list, tuple)):
            # Already a list/tuple like [256, 1] or (num_elec, 1)
            spatial = dim[0]
            temporal = dim[1] if len(dim) > 1 else 1
            if isinstance(spatial, str):
                try:
                    spatial = eval(spatial.replace('num_elec', str(num_elec)))
                except:
                    spatial = num_elec
            result.append([spatial, temporal])
        else:
            result.append([num_elec, 1])
    return result


def _parse_scaling(scaling_list, num_elec):
    """Parse scaling factor list with num_elec substitution.
    
    Args:
        scaling_list: List of scaling values (can contain "num_elec" strings or integers)
        num_elec: Number of electrodes
    
    Returns:
        List of integer scaling factors
    """
    if not scaling_list:
        return []
    
    result = []
    for val in scaling_list:
        if isinstance(val, str):
            try:
                result.append(eval(val.replace('num_elec', str(num_elec))))
            except:
                result.append(num_elec)
        else:
            result.append(val)
    return result


# Cache for PE config (loaded once)
_PE_CONFIG_CACHE = None


def _get_pe_config():
    """Get cached PE config or load it."""
    global _PE_CONFIG_CACHE
    if _PE_CONFIG_CACHE is None:
        _PE_CONFIG_CACHE = _load_pe_config()
    return _PE_CONFIG_CACHE


def _deep_update(dst: dict, src: dict) -> dict:
    """Recursively update dst with src (mutates dst) and return dst."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _component_dir_for(comp_type: str) -> str:
    # Prefer the type-specific directory if present; fall back to trx for legacy behavior.
    preferred = os.path.join('lib', 'Input', 'HW_components', comp_type)
    if os.path.isdir(preferred):
        return preferred
    return os.path.join('lib', 'Input', 'HW_components', 'trx')


def _choose_matching_transceiver(existing: dict, candidates: list):
    etype = existing.get('type')
    emethod = existing.get('method')
    if not etype:
        return None

    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        if cand.get('type') == etype and cand.get('method') == emethod:
            return cand

    by_type = [cand for cand in candidates if isinstance(cand, dict) and cand.get('type') == etype]
    if len(by_type) == 1:
        return by_type[0]
    return None


def _apply_power_transceivers(node_pm: dict, pm_spec: dict) -> None:
    spec_trxs = pm_spec.get('transceivers')
    if not isinstance(spec_trxs, list) or not spec_trxs:
        return
    if not isinstance(node_pm.get('transceivers'), list):
        return

    updated = []
    for tr in node_pm['transceivers']:
        if not isinstance(tr, dict):
            updated.append(tr)
            continue
        if tr.get('method') == 'none':
            updated.append(tr)
            continue
        cand = _choose_matching_transceiver(tr, spec_trxs)
        if cand is None:
            updated.append(tr)
            continue
        merged = copy.deepcopy(tr)
        merged.update(copy.deepcopy(cand))
        updated.append(merged)
    node_pm['transceivers'] = updated


def _apply_power_energy_storage(node_pm: dict, pm_spec: dict) -> None:
    es_spec = pm_spec.get('energy_storage')
    if not isinstance(es_spec, dict) or not es_spec:
        return
    if not isinstance(node_pm.get('energy_storage'), dict):
        return
    _deep_update(node_pm['energy_storage'], es_spec)


def _timing_log_path() -> str:
    # One file per process to avoid write contention under multiprocessing.
    base_dir = os.path.join('data_DSE', 'timing')
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f'netrun_{os.getpid()}.jsonl')


def _append_timing_record(record: dict) -> None:
    try:
        with open(_timing_log_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')
    except Exception:
        # Timing should never break the run.
        return

def run_sim_silent(args):
    with open(os.devnull, 'w') as fnull:
        with redirect_stdout(fnull), redirect_stderr(fnull):
            return run_sim(args)
        
def run_sim(args):
    # Support both 3-tuple (legacy) and 4-tuple (with workload) formats
    if len(args) == 4:
        component_file, num_elec, charge_times, wl = args
        return run_simulation(wl, component_file, num_elec, charge_times, component_types, optimize_metric)
    else:
        component_file, num_elec, charge_times = args
        return run_simulation(workload, component_file, num_elec, charge_times, component_types, optimize_metric)

def run_simulation(workload, component_file, num_elec, charge_times, component_types, optimize_metric):
    sys.path.append('lib')
    import Node.node as node
    import Network.network as network
    import PropagationChannel.propagationChannel as propagationChannel
    import profiler

    application_name = workload.split('_')[3]
    workload_path = f'lib/Input/{application_name}/{workload}.yaml'
    if not os.path.exists(workload_path):
        raise FileNotFoundError(f"Workload file '{workload_path}' does not exist.")
    with open(workload_path, 'r') as f:
        workload_config = yaml.full_load(f)

    component_spec = None
    if component_types in ['processor']:
        with open(f'lib/Input/HW_components/{component_types}/{application_name}/{component_file}', 'r') as f:
            component_spec = yaml.full_load(f)
    elif component_types in TRX_LIKE:
        component_dir = _component_dir_for(component_types)
        with open(os.path.join(component_dir, component_file), 'r') as f:
            component_spec = yaml.full_load(f)
    
    workload_config = copy.deepcopy(workload_config)

    if component_types == 'trx':
        # Apply a communication transceiver spec onto each node's comm_link.
        if isinstance(component_spec, dict) and isinstance(component_spec.get('transceivers'), list):
            for node_cfg in workload_config.get('hardware_spec', {}).get('nodes', []):
                if isinstance(node_cfg, dict) and isinstance(node_cfg.get('comm_link'), dict):
                    node_cfg['comm_link']['transceivers'] = copy.deepcopy(component_spec['transceivers'])

    elif component_types == 'power':
        # Apply power transfer unit spec by matching each node power transceiver (type/method).
        if isinstance(component_spec, dict) and isinstance(component_spec.get('power_management'), dict):
            pm_spec = component_spec.get('power_management')
            for node_cfg in workload_config.get('hardware_spec', {}).get('nodes', []):
                if isinstance(node_cfg, dict) and isinstance(node_cfg.get('power_management'), dict):
                    _apply_power_transceivers(node_cfg['power_management'], pm_spec)
                    _apply_power_energy_storage(node_cfg['power_management'], pm_spec)

    elif component_types == 'env':
        # Overlay environment fields.
        if isinstance(component_spec, dict) and isinstance(component_spec.get('environment'), dict):
            if not isinstance(workload_config.get('environment'), dict):
                workload_config['environment'] = {}
            _deep_update(workload_config['environment'], component_spec['environment'])

    elif component_types == 'processor':
        # Update the hardware spec - processors
        workload_config['hardware_spec']['nodes'][0]['processor'] = copy.deepcopy(component_spec['processor'][0])
        workload_config['hardware_spec']['nodes'][1]['processor'] = copy.deepcopy(component_spec['processor'][1])
        workload_config['hardware_spec']['nodes'][2]['processor'] = copy.deepcopy(component_spec['processor'][2])

        # Record the number of kernels for each node
        workload_config['hardware_spec']['nodes'][0]['processor']['num_kernels'] = component_spec['split'][0]
        workload_config['hardware_spec']['nodes'][1]['processor']['num_kernels'] = component_spec['split'][1]
        workload_config['hardware_spec']['nodes'][2]['processor']['num_kernels'] = component_spec['split'][2]

        # Save yaml file with the processor spec
        if creating_workloads:
            split_num_list = component_spec['split']
            file_path = os.path.join(f'lib/Input/{application_name}', f'{workload}_{split_num_list[0]}{split_num_list[1]}{split_num_list[2]}.yaml')
            with open(file_path, 'w') as f:
                print(f'Creating file: {file_path}')
                yaml.dump(workload_config, f)
            return

    else:
        raise ValueError(f'Invalid component type: {component_types}. Supported types are: trx, processor, power, env')
    
    # common spec - scale with number of electrodes
    if component_types in TRX_LIKE:
        workload_config['hardware_spec']['nodes'][0]['sensor']['electrodes'] = num_elec

        # Get application-specific dimension configuration from PEs.yaml
        pe_config = _get_pe_config()
        app_cfg = pe_config.get(application_name, {})
        
        # Parse dimensions with num_elec substitution
        raw_dimensions = app_cfg.get('dimensions', [])
        dimensions = _parse_dimensions(raw_dimensions, num_elec)
        strides = app_cfg.get('strides', [])
        raw_scaling = app_cfg.get('scaling', [])
        scaling_factor = _parse_scaling(raw_scaling, num_elec)
        
        # Update the dimension of the processor
        offset = 0
        raw_data = True
        for i in range(3):
            num_pe = workload_config['hardware_spec']['nodes'][i]['processor']['num_kernels']
            if num_pe > 0:
                raw_data = False
            if not raw_data:
                workload_config['hardware_spec']['nodes'][i]['processor']['kernel_dimensions'] = copy.deepcopy(dimensions[offset:offset + num_pe])
                workload_config['hardware_spec']['nodes'][i]['processor']['kernel_strides'] = copy.deepcopy(strides[offset:offset + num_pe])
                workload_config['hardware_spec']['nodes'][i]['processor']['output_spatial'] = dimensions[offset + num_pe - 1][0]
                workload_config['hardware_spec']['nodes'][i]['processor']['output_temporal'] = dimensions[offset + num_pe - 1][1]
            else:
                workload_config['hardware_spec']['nodes'][i]['processor']['output_spatial'] = num_elec
                
            workload_config['hardware_spec']['nodes'][i]['comm_link']['protocol']['dynamic_power'] *= workload_config['hardware_spec']['nodes'][i]['processor']['output_spatial'] / 1024
            workload_config['hardware_spec']['nodes'][i]['processor']['dynamic_power'] *= num_elec / 1024
            
            revision = True
            if revision:
                workload_config['hardware_spec']['nodes'][i]['processor']['dynamic_power'] = 0

            for j in range(num_pe):
                if revision:
                    workload_config['hardware_spec']['nodes'][i]['processor']['kernel_powers'][j] *= scaling_factor[offset + j] / 1024
                    workload_config['hardware_spec']['nodes'][i]['processor']['dynamic_power'] += workload_config['hardware_spec']['nodes'][i]['processor']['kernel_powers'][j]
                else:
                    workload_config['hardware_spec']['nodes'][i]['processor']['kernel_powers'][j] *= num_elec / 1024
                        
            offset += num_pe
        
        assert offset == len(dimensions), f"Offset {offset} does not match dimensions length {len(dimensions)}"

        # Find and modify the off_body processor
        if workload_config['hardware_spec']['nodes'][2]['location'] == 'external':
            processor = workload_config['hardware_spec']['nodes'][2]['processor']
            # Reduce output_latency by 0.2x (multiply by 0.2)
            if 'output_latency' in processor:
                processor['output_latency'] *= 0.2
                for i in range(len(processor['kernel_latencies'])):
                    processor['kernel_latencies'][i] *= 0.2
            # Increase dynamic_power by 5x
            if 'dynamic_power' in processor:
                processor['dynamic_power'] *= 5
                for i in range(len(processor['kernel_powers'])):
                    processor['kernel_powers'][i] *= 5
            # Increase static_power by 5x
            if 'static_power' in processor:
                processor['static_power'] *= 5

        # apply following only when optimize metric is operatingtime
        if optimize_metric == 'operatingtime':
            # set charge times, max_lifetime and initial charge
            max_lifetime = 24 - charge_times  # in hours
            charge_time = charge_times  # in hours
            radiated_power = 20  # in mW from near-implant to on-implant
            initial_radiated_power = 200  # in mW from charger to on-implant
            min_charge_status = 0  # in percent
            
            # Calculate initial charge based on energy storage type
            energy_storage_name = workload.split('_')[2]
            storage_configs = {
                'SMALL-BAT': (0.2, 0.8),   # (capacity_wh, efficiency)
                'BAT': (2.0, 0.8),
                'SMALL-CAP': (0.0002, 0.95),
            }
            
            # change near_implant radiated_power
            workload_config['hardware_spec']['nodes'][1]['power_management']['transceivers'][0]['radiated_power'] = radiated_power
            
            if energy_storage_name in storage_configs:
                capacity, efficiency = storage_configs[energy_storage_name]
                charged_energy = initial_radiated_power / 1000 * 10 ** (-10 / 10) * 0.9 * efficiency * charge_time
                initial_charge = min(charged_energy / capacity * 100 + min_charge_status, 100)
                es = workload_config['hardware_spec']['nodes'][0]['power_management']['energy_storage']
                es['initial_charge'] = initial_charge
                es['max_lifetime'] = max_lifetime
                es['charge_time'] = charge_time



        if dump_yaml:
            file_path = os.path.join(f'lib/Input/Runspace_{component_types}/{application_name}', f'{workload}_{component_file.replace(".yaml", "")}_{num_elec}_{charge_times}.yaml')
            with open(file_path, 'w') as f:
                print(f'Creating file: {file_path}')
                yaml.dump(workload_config, f)

    application_qos = workload_config['application']
    hardware_requirement = workload_config['hardware_spec']
    environment = workload_config['environment']

    # Get power constraints from TierX config
    power_constraints = tierx_config.get('power_constraints', {})
    
    # Get input scaling configuration from TierX config
    input_scaling = tierx_config.get('input_scaling', {})
    
    # Get pipelining configuration from TierX config
    pipelining = tierx_config.get('pipelining', {})
    
    # Get SRAM model configuration from TierX config
    sram_model = tierx_config.get('sram_model', {'enabled': False})

    num_nodes = hardware_requirement['num_nodes']
    node_spec_list, nodes = [], []
    node_id = 0
    for d in range(len(hardware_requirement['nodes'])):
        for node_name in hardware_requirement['nodes'][d]['name'].split(','):
            # Add power_constraints, input_scaling, pipelining, and sram_model to node args
            node_args = copy.deepcopy(hardware_requirement['nodes'][d])
            node_args['power_constraints'] = power_constraints
            node_args['input_scaling'] = input_scaling
            node_args['pipelining'] = pipelining
            node_args['sram_model'] = sram_model
            node_tmp = node.Node(node_args, node_name, node_id)
            nodes.append(node_tmp)
            node_spec_list.append(hardware_requirement['nodes'][d])
            node_id += 1

    prop_channels = {}
    for d1 in range(num_nodes):
        for d2 in range(num_nodes):
            if d1 != d2:
                if d1 in prop_channels:
                    prop_channels[d1][d2] = propagationChannel.PropagationChannel(node_spec_list[d1], node_spec_list[d2], environment)
                else:
                    prop_channels[d1] = {d2: propagationChannel.PropagationChannel(node_spec_list[d1], node_spec_list[d2], environment)}

    net = network.Network(nodes, application_qos, hardware_requirement, prop_channels, environment)

    eval_t0 = time.perf_counter()
    netrun_t0 = time.perf_counter()
    success = False
    err_type = None
    BER = retransmission_num = lifetime = latency = implant_power_consumption = power_breakdown = latency_breakdown = None
    try:
        BER, retransmission_num, lifetime, latency, implant_power_consumption, power_breakdown, latency_breakdown = net.run()
        success = True
    except SystemExit:
        err_type = 'SystemExit'
        return None
    except Exception as e:
        err_type = type(e).__name__
        raise
    finally:
        netrun_t1 = time.perf_counter()
        eval_t1 = time.perf_counter()
        _append_timing_record({
            'application': str(application_name),
            'component_type': str(component_types),
            'optimize_metric': str(optimize_metric) if optimize_metric is not None else 'None',
            'workload': str(workload),
            'component_file': str(component_file),
            'num_elec': int(num_elec),
            'charge_times': int(charge_times),
            'net_run_s': float(netrun_t1 - netrun_t0),
            'eval_total_s': float(eval_t1 - eval_t0),
            'success': bool(success),
            'error': err_type,
            'pid': int(os.getpid()),
        })

    config_name = f'{workload}_{component_file.replace(".yaml", "")}_{num_elec}'
    peak_power_violation, avg_power_violation, latency_violation = profiler.PRINT_STATS(net, config_name)

    if component_types in TRX_LIKE:
        return (BER, num_elec, peak_power_violation, avg_power_violation, latency_violation, component_file, lifetime, latency, implant_power_consumption, power_breakdown, latency_breakdown)
    elif component_types == 'processor':
        # read how PEs are splited in the component spec
        split_num_list = component_spec['split']
        # calculate offloading score with inner product of the split_num_list and [1,10,100]
        offloading_score = sum([split_num_list[i] * (10 ** i) for i in range(len(split_num_list))])
        return (offloading_score, num_elec, peak_power_violation, avg_power_violation, latency_violation)
    else:
        raise ValueError(f'Invalid component type: {component_types}. Supported types are: trx, processor, power, env')


def select_electrodes(app_name: str, comp_type: str, metric: str):
    dse_cfg = search_space.get('dse', {}) if isinstance(search_space, dict) else {}
    electrode_cfg = dse_cfg.get('electrodes', {}) if isinstance(dse_cfg, dict) else {}

    if comp_type in TRX_LIKE and metric == 'throughput':
        throughput_cfg = electrode_cfg.get('throughput', {}) if isinstance(electrode_cfg, dict) else {}
        if isinstance(throughput_cfg, dict):
            if app_name in throughput_cfg:
                return throughput_cfg[app_name]
            if 'default' in throughput_cfg:
                return throughput_cfg['default']
        # fallback to previous hardcoded ranges
        if app_name in ('SpikeSorting', 'GRU'):
            return list(range(10, 100, 10)) + list(range(100, 2000, 100))
        return list(range(100, 600, 100)) + list(range(600, 10000, 200))

    if isinstance(electrode_cfg, dict) and 'default' in electrode_cfg:
        return electrode_cfg['default']
    return [200]


def select_charge_times(comp_type: str, metric: str):
    dse_cfg = search_space.get('dse', {}) if isinstance(search_space, dict) else {}
    charge_cfg = dse_cfg.get('charge_times', {}) if isinstance(dse_cfg, dict) else {}

    if comp_type in TRX_LIKE and metric == 'operatingtime':
        if isinstance(charge_cfg, dict) and 'operatingtime' in charge_cfg:
            return charge_cfg['operatingtime']
        return list(range(1, 25, 1))

    return [1]


def load_component_files(comp_type: str, app_name: str, workload_name: str):
    if comp_type == 'processor':
        return [f for f in os.listdir(f'lib/Input/HW_components/{comp_type}/{app_name}') if f.endswith('.yaml')]
    if comp_type in TRX_LIKE:
        component_dir = _component_dir_for(comp_type)
        files = [f for f in os.listdir(component_dir) if f.endswith('.yaml')]
        if comp_type == 'trx':
            # Keep component sweep aligned with the workload's selected comm method.
            # Workload naming convention includes TierX comm method tokens like:
            #   - HIGH-BCC / LOW-BCC / LOW-RF
            if 'HIGH-BCC' in workload_name:
                files = [f for f in files if 'BCC' in f and '80Mbps' in f]
            elif 'LOW-BCC' in workload_name:
                files = [f for f in files if 'BCC' in f and '20Mbps' in f]
            elif 'LOW-RF' in workload_name:
                files = [f for f in files if 'RF' in f and '20Mbps' in f]
            else:
                # Fallback: coarse filter by PHY method.
                if 'BCC' in workload_name:
                    files = [f for f in files if 'BCC' in f]
                elif 'RF' in workload_name:
                    files = [f for f in files if 'RF' in f]
            return [f for f in files if not ('BCC' in f and 'EXTERNAL' in workload_name)]

        if comp_type == 'power':
            # Workload naming convention: <node_placement>_<comm_method>_<power_source>_<app>[_...]
            parts = workload_name.split('_')
            power_source = parts[2] if len(parts) >= 4 else None
            if power_source:
                scoped = [f for f in files if f.startswith(f'{power_source}_energy_density_')]
                if scoped:
                    return scoped
            if 'default.yaml' in files:
                return ['default.yaml']
            return files

        return files
    raise ValueError(f'Invalid component type: {comp_type}. Supported types are: trx, processor, power, env')


def should_keep_result(result, pruning_config=None):
    """Check if result should be kept based on pruning conditions.
    
    Args:
        result: Simulation result tuple
        pruning_config: Dict with pruning settings (check_ber, check_power_violation, check_avg_power_violation, check_latency_violation)
                       If None, uses default strict pruning (all checks enabled)
    """
    if result is None:
        return False
    
    # Default pruning config (strict)
    if pruning_config is None:
        pruning_config = {
            'check_ber': True,
            'check_power_violation': True,
            'check_avg_power_violation': False,  # Disabled by default for backward compatibility
            'check_latency_violation': True
        }
    
    ber, _, peak_power_v, avg_power_v, latency_v, *_ = result
    
    # Apply configurable checks
    if pruning_config.get('check_ber', True) and ber is None:
        return False
    if pruning_config.get('check_power_violation', True) and peak_power_v:
        return False
    if pruning_config.get('check_avg_power_violation', False) and avg_power_v:
        return False
    if pruning_config.get('check_latency_violation', True) and latency_v:
        return False
    
    return True


class SmartPruner:
    """Smart pruning with branch-and-bound style optimization.
    
    Tracks failure patterns and prunes configurations that would definitely fail:
    1. Power-based: If implant power X causes power violation, skip configs with higher implant power
    2. Electrode-based: For throughput optimization, if X electrodes fails, skip configs with more electrodes
    3. Processor-based: If a processor split with X implant kernels fails, skip splits with more implant kernels
    """
    
    def __init__(self, metric, pruning_config=None, component_type='trx'):
        self.metric = metric
        self.component_type = component_type
        self.pruning_config = pruning_config or {
            'check_ber': True,
            'check_power_violation': True,
            'check_avg_power_violation': False,  # Disabled by default for backward compatibility
            'check_latency_violation': True
        }
        
        # Track failure thresholds per component file
        # Key: component_file, Value: min electrodes that caused failure
        self.electrode_failure_threshold = {}  # For throughput metric
        
        # Track power violation thresholds (peak power)
        # Key: component_file, Value: min implant power that caused power violation
        self.power_failure_threshold = {}
        
        # Track average power violation thresholds
        # Key: component_file, Value: min electrodes that caused avg power violation
        self.avg_power_failure_threshold = {}
        
        # Track processor split failures
        # Key: (electrodes, charge_time), Value: min implant_kernels (split[0]) that caused failure
        self.processor_implant_threshold = {}
        
        # Statistics
        self.total_tasks = 0
        self.evaluated_tasks = 0
        self.pruned_tasks = 0
        self.valid_results = 0
    
    def _get_implant_kernels_from_component(self, component_file):
        """Extract the number of implant kernels from a processor component file name.
        
        Processor files are named like 'split_X_Y_Z.yaml' where X is implant kernels.
        """
        try:
            # Parse component file name to get split info
            # E.g., 'split_5_1_1.yaml' -> implant_kernels = 5
            if 'split_' in component_file:
                parts = component_file.replace('.yaml', '').split('_')
                if len(parts) >= 2:
                    return int(parts[1])  # First number after 'split_' is implant kernels
        except (ValueError, IndexError):
            pass
        return None
    
    def should_skip(self, component_file, electrodes, charge_time):
        """Check if this configuration should be skipped based on learned failure patterns.
        
        Returns:
            (skip: bool, reason: str or None)
        """
        # Electrode-based pruning for throughput optimization (TRX-like components)
        if self.metric == 'throughput' and component_file in self.electrode_failure_threshold:
            threshold = self.electrode_failure_threshold[component_file]
            if electrodes >= threshold:
                return True, f"electrode_pruned (>= {threshold})"
        
        # Power-based pruning (peak power): if we know this component causes power violations at certain electrode counts
        if self.pruning_config.get('check_power_violation', True):
            if component_file in self.power_failure_threshold:
                power_threshold_electrodes = self.power_failure_threshold[component_file]
                if electrodes >= power_threshold_electrodes:
                    return True, f"power_pruned (>= {power_threshold_electrodes} electrodes)"
        
        # Average power-based pruning: if we know this component causes avg power violations at certain electrode counts
        if self.pruning_config.get('check_avg_power_violation', False):
            if component_file in self.avg_power_failure_threshold:
                avg_power_threshold_electrodes = self.avg_power_failure_threshold[component_file]
                if electrodes >= avg_power_threshold_electrodes:
                    return True, f"avg_power_pruned (>= {avg_power_threshold_electrodes} electrodes)"
        
        # Processor-based pruning: skip splits with more implant kernels than failed threshold
        if self.component_type == 'processor':
            key = (electrodes, charge_time)
            if key in self.processor_implant_threshold:
                threshold = self.processor_implant_threshold[key]
                implant_kernels = self._get_implant_kernels_from_component(component_file)
                if implant_kernels is not None and implant_kernels >= threshold:
                    return True, f"processor_pruned (implant_kernels >= {threshold})"
        
        return False, None
    
    def record_result(self, component_file, electrodes, charge_time, result):
        """Record a simulation result to update pruning thresholds.
        
        Args:
            component_file: The component configuration file
            electrodes: Number of electrodes in this config
            charge_time: Charge time in this config
            result: The simulation result tuple
        """
        if result is None:
            # Simulation failed completely
            if self.metric == 'throughput':
                self._update_electrode_threshold(component_file, electrodes)
            if self.component_type == 'processor':
                self._update_processor_threshold(component_file, electrodes, charge_time)
            return
        
        # Handle processor results differently (no BER field)
        if self.component_type == 'processor':
            offloading_score, num_elec, peak_power_v, avg_power_v, latency_v = result
            
            # Peak power violation - more implant kernels will have even more power consumption
            if peak_power_v and self.pruning_config.get('check_power_violation', True):
                self._update_processor_threshold(component_file, electrodes, charge_time)
                self._update_power_threshold(component_file, electrodes)
            
            # Average power violation
            if avg_power_v and self.pruning_config.get('check_avg_power_violation', False):
                self._update_processor_threshold(component_file, electrodes, charge_time)
                self._update_avg_power_threshold(component_file, electrodes)
            return
        
        # TRX-like results
        ber, num_elec, peak_power_v, avg_power_v, latency_v, *rest = result
        
        # BER failure (None) - for throughput, higher electrodes will likely also fail
        if ber is None and self.metric == 'throughput':
            self._update_electrode_threshold(component_file, electrodes)
        
        # Peak power violation - higher electrode counts will have even more power consumption
        if peak_power_v and self.pruning_config.get('check_power_violation', True):
            self._update_power_threshold(component_file, electrodes)
        
        # Average power violation - higher electrode counts will have even more average power consumption
        if avg_power_v and self.pruning_config.get('check_avg_power_violation', False):
            self._update_avg_power_threshold(component_file, electrodes)
    
    def _update_electrode_threshold(self, component_file, electrodes):
        """Update electrode failure threshold - lower is more restrictive."""
        if component_file not in self.electrode_failure_threshold:
            self.electrode_failure_threshold[component_file] = electrodes
        else:
            # Keep the minimum (most restrictive) threshold
            self.electrode_failure_threshold[component_file] = min(
                self.electrode_failure_threshold[component_file], electrodes
            )
    
    def _update_power_threshold(self, component_file, electrodes):
        """Update peak power failure threshold - lower is more restrictive."""
        if component_file not in self.power_failure_threshold:
            self.power_failure_threshold[component_file] = electrodes
        else:
            self.power_failure_threshold[component_file] = min(
                self.power_failure_threshold[component_file], electrodes
            )
    
    def _update_avg_power_threshold(self, component_file, electrodes):
        """Update average power failure threshold - lower is more restrictive."""
        if component_file not in self.avg_power_failure_threshold:
            self.avg_power_failure_threshold[component_file] = electrodes
        else:
            self.avg_power_failure_threshold[component_file] = min(
                self.avg_power_failure_threshold[component_file], electrodes
            )
    
    def _update_processor_threshold(self, component_file, electrodes, charge_time):
        """Update processor implant kernel threshold - lower is more restrictive."""
        implant_kernels = self._get_implant_kernels_from_component(component_file)
        if implant_kernels is None:
            return
        
        key = (electrodes, charge_time)
        if key not in self.processor_implant_threshold:
            self.processor_implant_threshold[key] = implant_kernels
        else:
            self.processor_implant_threshold[key] = min(
                self.processor_implant_threshold[key], implant_kernels
            )
    
    def get_stats(self):
        """Return pruning statistics."""
        stats = {
            'total_tasks': self.total_tasks,
            'evaluated_tasks': self.evaluated_tasks,
            'pruned_tasks': self.pruned_tasks,
            'valid_results': self.valid_results,
            'prune_rate': f"{100 * self.pruned_tasks / max(1, self.total_tasks):.1f}%",
            'electrode_thresholds': dict(self.electrode_failure_threshold),
            'power_thresholds': dict(self.power_failure_threshold),
        }
        if self.component_type == 'processor' and self.processor_implant_threshold:
            # Convert tuple keys to string for JSON compatibility
            stats['processor_thresholds'] = {
                f"elec={k[0]}_ct={k[1]}": v for k, v in self.processor_implant_threshold.items()
            }
        return stats


def _get_implant_kernels(component_file):
    """Extract the number of implant kernels from a processor component file name.
    
    Processor files are named like 'split_X_Y_Z.yaml' where X is implant kernels.
    """
    try:
        if 'split_' in component_file:
            parts = component_file.replace('.yaml', '').split('_')
            if len(parts) >= 2:
                return int(parts[1])  # First number after 'split_' is implant kernels
    except (ValueError, IndexError):
        pass
    return 0


def _run_task_with_index(args):
    """Helper function for smart_pruning_search - must be at module level for pickling."""
    idx, task, run_sim_func = args
    return (idx, run_sim_func(task))


def _run_task_with_shared_state(args):
    """Worker function that checks shared pruning state before running.
    
    Returns (idx, result, should_have_pruned) where should_have_pruned indicates
    if this task was actually pruned based on thresholds learned during execution.
    """
    idx, task, run_sim_func, thresholds_dict, component_type = args
    component_file, electrodes, charge_time = task
    
    # Check if this task should be skipped based on current thresholds
    if component_type == 'processor':
        # Inline _get_implant_kernels logic to avoid import issues in worker process
        kernel_count = 0
        try:
            if component_file.startswith('split_'):
                parts = component_file.replace('.yaml', '').split('_')
                kernel_count = int(parts[1]) if len(parts) >= 2 else 0
        except (ValueError, IndexError):
            pass
        
        key = str((electrodes, charge_time))
        threshold = thresholds_dict.get(key)
        if threshold is not None and kernel_count > threshold:
            return (idx, None, True)  # Pruned
    else:
        threshold = thresholds_dict.get(component_file)
        if threshold is not None and electrodes > threshold:
            return (idx, None, True)  # Pruned
    
    # Run the simulation
    result = run_sim_func(task)
    return (idx, result, False)


def _run_task_simple(task):
    """Simple task runner for concurrent.futures - takes (run_func, task) tuple."""
    run_func, actual_task = task
    return run_func(actual_task)


def smart_pruning_search(tasks, run_sim_func, metric, pruning_config=None, component_type='trx'):
    """Execute smart pruning search with progressive submission for actual computation savings.
    
    Uses concurrent.futures.ProcessPoolExecutor with progressive task submission:
    1. Submit tasks in batches sorted by electrode/kernel count (ascending)
    2. As results complete, update failure thresholds
    3. Before submitting each new batch, filter out tasks that would be pruned
    4. This achieves real computation savings with minimal overhead
    
    1. Processor-based pruning: If a workload partition with K on-implant kernels causes 
       safety budget violation, skip all partitions with MORE on-implant kernels (> K).
    
    2. Electrode-based pruning: If electrode count E causes violation, skip all simulation 
       runs with MORE electrodes (> E) for the same design/component.
    
    Args:
        tasks: List of (component_file, electrodes, charge_time) tuples
        run_sim_func: Function to run simulation
        metric: Optimization metric (throughput, latency, operatingtime)
        pruning_config: Pruning configuration dict
        component_type: Component type ('trx', 'processor', 'power', 'env')
        
    Returns:
        (results: list, pruner: SmartPruner with stats)
    """
    from itertools import groupby
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    pruner = SmartPruner(metric, pruning_config, component_type)
    results = []
    
    pruner.total_tasks = len(tasks)
    
    if not tasks:
        return results, pruner
    
    # Determine batch key extractor based on component type
    if component_type == 'processor':
        def get_batch_key(task):
            component_file, electrodes, charge_time = task
            return _get_implant_kernels(component_file)
        
        def should_prune(task, thresholds):
            component_file, electrodes, charge_time = task
            key = (electrodes, charge_time)
            kernel_count = _get_implant_kernels(component_file)
            if key in thresholds and kernel_count > thresholds[key]:
                return True
            return False
        
        def update_threshold(task, thresholds, pruner_obj):
            component_file, electrodes, charge_time = task
            key = (electrodes, charge_time)
            kernel_count = _get_implant_kernels(component_file)
            if key not in thresholds:
                thresholds[key] = kernel_count
                pruner_obj.processor_implant_threshold[key] = kernel_count
    else:
        def get_batch_key(task):
            component_file, electrodes, charge_time = task
            return electrodes
        
        def should_prune(task, thresholds):
            component_file, electrodes, charge_time = task
            if component_file in thresholds and electrodes > thresholds[component_file]:
                return True
            return False
        
        def update_threshold(task, thresholds, pruner_obj):
            component_file, electrodes, charge_time = task
            if component_file not in thresholds:
                thresholds[component_file] = electrodes
                pruner_obj.electrode_failure_threshold[component_file] = electrodes
    
    # Sort tasks by batch key (ascending) so lower values are processed first
    sorted_tasks = sorted(enumerate(tasks), key=lambda x: get_batch_key(x[1]))
    
    # Group tasks into batches by batch key value
    batches = []
    for batch_key, group in groupby(sorted_tasks, key=lambda x: get_batch_key(x[1])):
        batches.append((batch_key, list(group)))
    
    # Track failure thresholds
    failure_thresholds = {}
    
    # For processor: use sequential execution (same as exhaustive) for compatibility
    if component_type == 'processor':
        # Sequential execution for processor (same as exhaustive)
        for batch_key, batch_items in tqdm(batches, desc="Pruning batches"):
            for orig_idx, task in batch_items:
                if should_prune(task, failure_thresholds):
                    pruner.pruned_tasks += 1
                    continue
                
                # Run task
                res = run_sim_func(task)
                pruner.evaluated_tasks += 1
                
                if res is None or not should_keep_result(res, pruning_config):
                    update_threshold(task, failure_thresholds, pruner)
                else:
                    results.append(res)
                    pruner.valid_results += 1
    else:
        # Progressive submission approach for non-processor types
        # Group batches into larger chunks for efficiency while still enabling pruning
        num_workers = cpu_count()
        
        # Chunk size: process multiple electrode counts together to reduce overhead
        # But not too many to still get pruning benefit
        chunk_size = max(1, len(batches) // 4)  # Split into ~4 chunks
        
        batch_chunks = []
        for i in range(0, len(batches), chunk_size):
            chunk = batches[i:i + chunk_size]
            batch_chunks.append(chunk)
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            with tqdm(total=len(tasks), desc="Pruning") as pbar:
                for chunk in batch_chunks:
                    # Collect all tasks in this chunk, filtering by current thresholds
                    chunk_tasks = []
                    for batch_key, batch_items in chunk:
                        for orig_idx, task in batch_items:
                            if should_prune(task, failure_thresholds):
                                pruner.pruned_tasks += 1
                                pbar.update(1)
                            else:
                                chunk_tasks.append((orig_idx, task))
                    
                    if not chunk_tasks:
                        continue
                    
                    # Submit all tasks in this chunk
                    future_to_idx = {}
                    for orig_idx, task in chunk_tasks:
                        future = executor.submit(run_sim_func, task)
                        future_to_idx[future] = orig_idx
                    
                    # Process results as they complete
                    for future in as_completed(future_to_idx):
                        orig_idx = future_to_idx[future]
                        orig_task = tasks[orig_idx]
                        
                        try:
                            res = future.result()
                        except Exception:
                            res = None
                        
                        pruner.evaluated_tasks += 1
                        pbar.update(1)
                        
                        if res is None or not should_keep_result(res, pruning_config):
                            update_threshold(orig_task, failure_thresholds, pruner)
                        else:
                            results.append(res)
                            pruner.valid_results += 1
    
    return results, pruner


def fitness(result, metric, pruning_config=None, apply_pruning=False):
    """Calculate fitness score for a result.
    
    Args:
        result: Simulation result tuple
        metric: Optimization metric (throughput, latency, operatingtime, implant_power)
        pruning_config: Dict with pruning settings (only used if apply_pruning=True)
        apply_pruning: If True, apply pruning filter before calculating fitness
    """
    if result is None:
        return -1e9
    
    # Apply pruning filter if enabled
    if apply_pruning and not should_keep_result(result, pruning_config):
        return -1e9
    
    ber, num_elec, peak_power_v, avg_power_v, latency_v, *_ = result
    if ber is None or peak_power_v or latency_v:
        return -1e9
    # Also check avg_power_violation if enabled in pruning_config
    if pruning_config and pruning_config.get('check_avg_power_violation', False) and avg_power_v:
        return -1e9
    # TRX-like result includes lifetime, latency, implant power
    if len(result) >= 9:
        _, _, _, _, _, _, lifetime, latency, implant_power, *_ = result
    else:
        lifetime, latency, implant_power = None, None, None

    if metric == 'operatingtime':
        return lifetime if lifetime is not None else -1e9
    if metric == 'implant_power':
        return -implant_power if implant_power is not None else -1e9
    if metric == 'throughput':
        return num_elec
    # for latency, lower latency is better
    return -latency if latency is not None else -1e9


def component_type_to_sweep_type(comp_type):
    """Map component type to sweep type name."""
    mapping = {
        'trx': 'communication',
        'power': 'power',
        'env': 'node',
    }
    return mapping.get(comp_type, 'unknown')


def extract_params_from_result(result, component_files, electrodes, charge_times, workloads=None):
    """Extract parameter information from simulation result tuple.
    
    Returns dict with: component_file, electrodes, charge_time, workload (if applicable)
    """
    if result is None or len(result) < 1:
        return {}
    
    params = {}
    
    try:
        # First element is either (wl_idx, comp_idx, elec, ct) or (comp_idx, elec, ct)
        first = result[0]
        if isinstance(first, tuple) and len(first) == 4:
            # Multi-workload format
            wl_idx, comp_idx, elec, ct = first
            if workloads and wl_idx < len(workloads):
                params['workload'] = workloads[wl_idx]
            if component_files and comp_idx < len(component_files):
                params['component_file'] = component_files[comp_idx]
            params['electrodes'] = int(elec)
            params['charge_time'] = int(ct)
        elif isinstance(first, tuple) and len(first) == 3:
            # Single-workload format
            comp_idx, elec, ct = first
            if component_files and comp_idx < len(component_files):
                params['component_file'] = component_files[comp_idx]
            params['electrodes'] = int(elec)
            params['charge_time'] = int(ct)
    except (IndexError, TypeError, ValueError):
        pass
    
    return params


def ga_search(component_files, electrodes, charge_times, metric, workloads=None, 
               pop_size=10, generations=5, crossover_rate=0.8, mutation_rate=0.1,
               elitism=2, early_stop_generations=3, tournament_size=3, adaptive_mutation=True,
               apply_pruning=False, pruning_config=None):
    """Optimized genetic algorithm search with caching, elitism, and early stopping.
    
    Key optimizations for faster convergence while maintaining near-optimal results:
    1. Elitism: Preserve top N solutions across generations
    2. Caching: Avoid re-evaluating identical configurations  
    3. Early stopping: Stop if no improvement for N generations
    4. Tournament selection: More efficient parent selection
    5. Adaptive mutation: Increase mutation when stuck in local optima
    6. Optional pruning: Filter invalid configurations during fitness evaluation
    
    Args:
        component_files: List of component files to choose from
        electrodes: List of electrode counts to choose from
        charge_times: List of charge times to choose from
        metric: Optimization metric (throughput, latency, operatingtime, implant_power)
        workloads: Optional list of workloads (processor splits) to include in search
        pop_size: Population size per generation
        generations: Number of evolutionary generations
        crossover_rate: Probability of crossover between parents (0.0-1.0)
        mutation_rate: Base probability of mutating each gene (0.0-1.0)
        elitism: Number of top solutions to preserve across generations
        early_stop_generations: Stop if no improvement for this many generations
        tournament_size: Size of tournament for parent selection
        adaptive_mutation: If True, increase mutation rate when stuck
        apply_pruning: If True, apply pruning filter during fitness evaluation
        pruning_config: Dict with pruning settings (check_ber, check_power_violation, check_latency_violation)
    
    Returns:
        Tuple of (results_list, best_solution_dict) where best_solution_dict contains:
        - 'genome': The best genome found
        - 'params': Dict with readable parameter names
        - 'fitness': The fitness score
        - 'result': The full simulation result
        - 'objective_value': The actual objective metric value
    """
    # If workloads provided, genome includes workload index; otherwise just (comp, elec, ct)
    include_workloads = workloads is not None and len(workloads) > 1
    
    # Cache for evaluated configurations to avoid duplicate evaluations
    eval_cache = {}  # genome -> (fitness_score, result)
    
    # Track best fitness for early stopping
    best_fitness_history = []
    current_mutation_rate = mutation_rate
    
    # Track global best solution
    global_best = {'fitness': -1e9, 'genome': None, 'result': None}
    
    # Sort electrodes for throughput-biased selection (highest first for throughput)
    sorted_electrodes = sorted(electrodes, reverse=(metric == 'throughput'))
    
    def genome_to_key(genome):
        """Convert genome to hashable cache key."""
        return tuple(genome)
    
    def random_genome():
        """Generate random genome, biased toward high electrodes for throughput metric."""
        if metric == 'throughput':
            # For throughput, bias toward higher electrode values (top 50% more likely)
            if random.random() < 0.7:
                # 70% chance: pick from top half of electrode values
                top_half = sorted_electrodes[:len(sorted_electrodes) // 2 + 1]
                elec = random.choice(top_half) if top_half else random.choice(electrodes)
            else:
                elec = random.choice(electrodes)
        else:
            elec = random.choice(electrodes)
        
        if include_workloads:
            return (random.randrange(len(workloads)), random.randrange(len(component_files)), elec, random.choice(charge_times))
        return (random.randrange(len(component_files)), elec, random.choice(charge_times))

    def crossover(g1, g2):
        if random.random() > crossover_rate:
            return g1, g2
        if include_workloads:
            # Uniform crossover: randomly choose each gene from either parent
            child1 = tuple(g1[i] if random.random() < 0.5 else g2[i] for i in range(4))
            child2 = tuple(g2[i] if random.random() < 0.5 else g1[i] for i in range(4))
            return child1, child2
        # 3-gene uniform crossover
        child1 = tuple(g1[i] if random.random() < 0.5 else g2[i] for i in range(3))
        child2 = tuple(g2[i] if random.random() < 0.5 else g1[i] for i in range(3))
        return child1, child2

    def mutate(genome, mut_rate):
        if include_workloads:
            wl_idx, comp_idx, elec, ct = genome
            if random.random() < mut_rate:
                wl_idx = random.randrange(len(workloads))
            if random.random() < mut_rate:
                comp_idx = random.randrange(len(component_files))
            if random.random() < mut_rate:
                # Smart electrode mutation: for throughput, bias toward higher values
                idx = electrodes.index(elec) if elec in electrodes else 0
                if metric == 'throughput':
                    # For throughput, prefer moving to higher electrode values
                    delta = random.choice([-1, 1, 2, 3, 4])  # Bias toward positive
                else:
                    delta = random.choice([-2, -1, 1, 2])
                new_idx = max(0, min(len(electrodes) - 1, idx + delta))
                elec = electrodes[new_idx]
            if random.random() < mut_rate:
                ct = random.choice(charge_times)
            return (wl_idx, comp_idx, elec, ct)
        else:
            comp_idx, elec, ct = genome
            if random.random() < mut_rate:
                comp_idx = random.randrange(len(component_files))
            if random.random() < mut_rate:
                idx = electrodes.index(elec) if elec in electrodes else 0
                if metric == 'throughput':
                    # For throughput, prefer moving to higher electrode values
                    delta = random.choice([-1, 1, 2, 3, 4])  # Bias toward positive
                else:
                    delta = random.choice([-2, -1, 1, 2])
                new_idx = max(0, min(len(electrodes) - 1, idx + delta))
                elec = electrodes[new_idx]
            if random.random() < mut_rate:
                ct = random.choice(charge_times)
            return (comp_idx, elec, ct)

    def genome_to_task(genome):
        """Convert genome to (component_file, electrode, charge_time) task tuple."""
        if include_workloads:
            wl_idx, comp_idx, elec, ct = genome
            comp = component_files[comp_idx]
            return (comp, elec, ct, workloads[wl_idx])
        else:
            comp_idx, elec, ct = genome
            comp = component_files[comp_idx]
            return (comp, elec, ct, None)

    def tournament_select(scored_pop, k=3):
        """Tournament selection: pick k random individuals, return the best."""
        tournament = random.sample(scored_pop, min(k, len(scored_pop)))
        return max(tournament, key=lambda x: x[0])[1]

    # Create initial population with bias toward high electrodes for throughput
    population = []
    if metric == 'throughput':
        # For throughput, seed population with some maximum electrode configurations
        max_electrode = max(electrodes)
        for comp_idx in range(min(len(component_files), pop_size // 3)):
            if include_workloads:
                for wl_idx in range(min(len(workloads), 2)):
                    population.append((wl_idx, comp_idx, max_electrode, charge_times[0]))
            else:
                population.append((comp_idx, max_electrode, charge_times[0]))
    
    # Fill remaining with random genomes
    while len(population) < pop_size:
        population.append(random_genome())
    
    all_results = []
    elite_genomes = []  # Track elite solutions
    
    # Create pool once and reuse across generations (reduces overhead significantly)
    pool = Pool(cpu_count())

    try:
        for gen in range(generations):
            # Separate cached and uncached genomes
            uncached_genomes = []
            cached_results = []
            
            for genome in population:
                key = genome_to_key(genome)
                if key in eval_cache:
                    cached_results.append((genome, eval_cache[key]))
                else:
                    uncached_genomes.append(genome)
            
            # Only evaluate uncached configurations
            if uncached_genomes:
                tasks_with_genomes = [(genome, genome_to_task(genome)) for genome in uncached_genomes]
                
                if include_workloads:
                    tasks = [(t[0], t[1], t[2], t[3]) for _, t in tasks_with_genomes]
                else:
                    tasks = [(t[0], t[1], t[2]) for _, t in tasks_with_genomes]
                
                # Parallel evaluation using persistent pool
                results = list(pool.imap(run_sim_silent, tasks))
                
                # Cache results and score
                for (genome, _), res in zip(tasks_with_genomes, results):
                    fit = fitness(res, metric, pruning_config=pruning_config, apply_pruning=apply_pruning)
                    key = genome_to_key(genome)
                    eval_cache[key] = (fit, res)
                    all_results.append(res)
                    
                    # Update global best
                    if fit > global_best['fitness']:
                        global_best['fitness'] = fit
                        global_best['genome'] = genome
                        global_best['result'] = res
            
            # Build scored population (combining cached and newly evaluated)
            scored = []
            for genome in population:
                key = genome_to_key(genome)
                fit, res = eval_cache[key]
                scored.append((fit, genome, res))
            
            # Also check cached results for global best (in case elite from previous gen)
            if fit > global_best['fitness']:
                global_best['fitness'] = fit
                global_best['genome'] = genome
                global_best['result'] = res
        
            scored.sort(key=lambda x: x[0], reverse=True)
            
            # Track best fitness for early stopping
            current_best = scored[0][0]
            best_fitness_history.append(current_best)
            
            # Early stopping check
            if len(best_fitness_history) >= early_stop_generations:
                recent = best_fitness_history[-early_stop_generations:]
                if all(f == recent[0] for f in recent) and recent[0] > -1e9:
                    print(f"  GA early stop at generation {gen+1}: no improvement for {early_stop_generations} generations")
                    break
            
            # Adaptive mutation: increase if stuck
            if adaptive_mutation and len(best_fitness_history) >= 2:
                if best_fitness_history[-1] == best_fitness_history[-2]:
                    current_mutation_rate = min(0.5, current_mutation_rate * 1.5)
                else:
                    current_mutation_rate = mutation_rate  # Reset to base rate
            
            # Elitism: preserve top solutions
            elite_genomes = [g for _, g, _ in scored[:elitism]]
            
            # Create next generation using tournament selection
            new_population = list(elite_genomes)  # Start with elites
            
            while len(new_population) < pop_size:
                # Tournament selection
                p1 = tournament_select(scored, tournament_size)
                p2 = tournament_select(scored, tournament_size)
                c1, c2 = crossover(p1, p2)
                new_population.append(mutate(c1, current_mutation_rate))
                if len(new_population) < pop_size:
                    new_population.append(mutate(c2, current_mutation_rate))
            
            population = new_population[:pop_size]
    
        # Report cache effectiveness (outside for loop, inside try)
        cache_hits = len(eval_cache) 
        total_evals = sum(1 for r in all_results if r is not None)
        print(f"  GA cache: {cache_hits} unique configs evaluated, {total_evals} total evaluations")
        
        # Build best solution info
        best_solution = None
        if global_best['genome'] is not None and global_best['result'] is not None:
            genome = global_best['genome']
            result = global_best['result']
            
            # Extract readable parameters from genome
            if include_workloads:
                wl_idx, comp_idx, elec, ct = genome
                params = {
                    'workload': workloads[wl_idx] if wl_idx < len(workloads) else 'unknown',
                    'component_file': component_files[comp_idx] if comp_idx < len(component_files) else 'unknown',
                    'electrodes': elec,
                    'charge_time': ct,
                }
            else:
                comp_idx, elec, ct = genome
                params = {
                    'component_file': component_files[comp_idx] if comp_idx < len(component_files) else 'unknown',
                    'electrodes': elec,
                    'charge_time': ct,
                }
            
            # Extract objective value from result
            objective_value = None
            objective_unit = ''
            if result is not None and len(result) >= 8:
                _, num_elec, _, _, _, lifetime, latency, implant_power, *_ = result
                if metric == 'operatingtime':
                    objective_value = lifetime
                    objective_unit = 'hours'
                elif metric == 'throughput':
                    objective_value = num_elec
                    objective_unit = 'electrodes'
                elif metric == 'latency':
                    objective_value = latency
                    objective_unit = 'ms'
                elif metric == 'implant_power':
                    objective_value = implant_power
                    objective_unit = 'mW'
                else:
                    objective_value = global_best['fitness']
                    objective_unit = ''
            
            best_solution = {
                'genome': genome,
                'params': params,
                'fitness': global_best['fitness'],
                'result': result,
                'objective_value': objective_value,
                'objective_unit': objective_unit,
                'metric': metric,
            }
            
            # Print optimal solution summary
            print(f"\n  ╔{'═'*60}╗")
            print(f"  ║{'OPTIMAL SOLUTION FOUND':^60}║")
            print(f"  ╠{'═'*60}╣")
            print(f"  ║ Metric: {metric:<50}║")
            if objective_value is not None:
                obj_str = f"{objective_value:.4f} {objective_unit}" if isinstance(objective_value, float) else f"{objective_value} {objective_unit}"
                print(f"  ║ Objective Value: {obj_str:<42}║")
            print(f"  ║ Fitness Score: {global_best['fitness']:<44.4f}║")
            print(f"  ╠{'═'*60}╣")
            print(f"  ║{'OPTIMAL PARAMETERS':^60}║")
            print(f"  ╠{'═'*60}╣")
            for key, value in params.items():
                val_str = str(value)[:45]
                print(f"  ║ {key}: {val_str:<52}║")
            print(f"  ╚{'═'*60}╝\n")

    finally:
        # Clean up the persistent pool
        pool.close()
        pool.join()

    return [res for res in all_results if res is not None], best_solution

if __name__ == '__main__':
    ga_cfg = {}
    if isinstance(search_space, dict):
        dse_cfg = search_space.get('dse', {})
        if isinstance(dse_cfg, dict):
            ga_cfg = dse_cfg.get('ga', {}) if isinstance(dse_cfg.get('ga', {}), dict) else {}
            pruning_cfg = dse_cfg.get('pruning', {}) if isinstance(dse_cfg.get('pruning', {}), dict) else {}

    parser = argparse.ArgumentParser(description='TierX DSE runner')
    parser.add_argument('application', choices=APPLICATION_TYPES, help='Application name')
    parser.add_argument('component_type', choices=COMPONENT_TYPES, help='Component type to sweep')
    parser.add_argument('optimize_metric_pos', nargs='?', choices=['throughput', 'operatingtime', 'latency', 'implant_power'], help='(optional legacy positional) optimization metric')
    parser.add_argument('--optimize-metric', choices=['throughput', 'operatingtime', 'latency', 'implant_power'], default=None, help='Optimization metric (overrides positional if provided)')
    parser.add_argument('--search', choices=['exhaustive', 'pruning', 'ga'], default='exhaustive', help='Search strategy')
    parser.add_argument('--pop-size', type=int, default=ga_cfg.get('pop_size', 12), help='GA population size')
    parser.add_argument('--generations', type=int, default=ga_cfg.get('generations', 8), help='GA generations')
    parser.add_argument('--crossover', type=float, default=ga_cfg.get('crossover', 0.85), help='GA crossover rate')
    parser.add_argument('--mutation', type=float, default=ga_cfg.get('mutation', 0.15), help='GA base mutation rate')
    parser.add_argument('--elitism', type=int, default=ga_cfg.get('elitism', 2), help='GA elitism: number of top solutions to preserve')
    parser.add_argument('--early-stop', type=int, default=ga_cfg.get('early_stop', 3), help='Stop if no improvement for N generations')
    parser.add_argument('--tournament-size', type=int, default=ga_cfg.get('tournament_size', 3), help='Tournament selection size')
    parser.add_argument('--no-adaptive-mutation', dest='adaptive_mutation', action='store_false', default=ga_cfg.get('adaptive_mutation', True), help='Disable adaptive mutation rate')
    parser.add_argument('--no-dump-yaml', dest='dump_yaml', action='store_false', help='Skip writing runspace YAMLs (faster, less disk)')
    parser.add_argument('--no-save-graph', dest='save_graph', action='store_false', help='Skip writing pickle graph outputs')
    args = parser.parse_args()

    desired_application = args.application
    component_types = args.component_type
    optimize_metric = args.optimize_metric or args.optimize_metric_pos
    search_strategy = args.search
    
    # Prepare pruning config after search_strategy is defined
    apply_pruning_in_ga = ga_cfg.get('apply_pruning', True) if search_strategy == 'ga' else False
    pruning_config = {
        'check_ber': pruning_cfg.get('check_ber', True),
        'check_power_violation': pruning_cfg.get('check_power_violation', True),
        'check_latency_violation': pruning_cfg.get('check_latency_violation', True),
    }
    dump_yaml = args.dump_yaml
    save_graph = args.save_graph

    if not os.path.exists('plots'):
        os.makedirs('plots')
    else:
        for file in os.listdir('plots'):
            file_path = os.path.join('plots', file)
            if os.path.isfile(file_path):
                os.remove(file_path)

    with open('src/run.yaml', 'r') as f:
        sim_config = yaml.full_load(f)
    
    # Make directory 'data_DSE/{search_strategy}' based on search strategy
    base_data_dir = 'data_DSE_motivation_application'
    data_dir = os.path.join(base_data_dir, search_strategy)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    # Collect valid workloads (excluding BCC+EXTERNAL combinations)
    valid_workloads = [w for w in sim_config['workloads'] 
                       if not ('BCC' in w and 'EXTERNAL' in w)]
    skipped_workloads = len(sim_config['workloads']) - len(valid_workloads)
    if skipped_workloads > 0:
        print(f'Skipping {skipped_workloads} workloads due to BCC and EXTERNAL combination')

    # Create runspace folders for all applications in workloads
    # This ensures directories exist for all workloads, not just desired_application
    if dump_yaml and component_types in TRX_LIKE:
        runspace_base = f'lib/Input/Runspace_{component_types}'
        if not os.path.exists(runspace_base):
            os.makedirs(runspace_base)
        # Extract unique application names from workloads
        app_names = set()
        for wl in valid_workloads:
            for app in APPLICATION_TYPES:
                if f'_{app}_' in wl or wl.endswith(f'_{app}'):
                    app_names.add(app)
                    break
        # Also include desired_application
        app_names.add(desired_application)
        # Create directories for each application
        for app in app_names:
            app_dir = os.path.join(runspace_base, app)
            if not os.path.exists(app_dir):
                os.makedirs(app_dir)
            else:
                # Clean existing files
                for file in os.listdir(app_dir):
                    file_path = os.path.join(app_dir, file)
                    if os.path.isfile(file_path):
                        os.remove(file_path)

    # For GA with multiple workloads: search across all workloads (processor splits) at once
    if search_strategy == 'ga' and len(valid_workloads) > 1 and component_types in TRX_LIKE:
        print(f'Running GA search across {len(valid_workloads)} workloads (processor splits)')
        
        # Use first workload to get electrode/charge_time ranges (same for all workloads of same app)
        electrodes = select_electrodes(desired_application, component_types, optimize_metric)
        charge_times = select_charge_times(component_types, optimize_metric)
        
        # Collect all component files (may vary per workload due to comm method filtering)
        # For simplicity, use union of all component files
        all_component_files = set()
        for wl in valid_workloads:
            all_component_files.update(load_component_files(component_types, desired_application, wl))
        component_files = sorted(list(all_component_files))
        
        total_search_space = len(valid_workloads) * len(component_files) * len(electrodes) * len(charge_times)
        print(f'Total search space: {len(valid_workloads)} workloads × {len(component_files)} components × {len(electrodes)} electrodes × {len(charge_times)} charge_times = {total_search_space}')
        print(f'GA will evaluate: {args.pop_size} × {args.generations} = {args.pop_size * args.generations} configurations')
        
        results, best_solution = ga_search(
            component_files,
            electrodes,
            charge_times,
            optimize_metric,
            workloads=valid_workloads,
            pop_size=args.pop_size,
            generations=args.generations,
            crossover_rate=args.crossover,
            mutation_rate=args.mutation,
            elitism=args.elitism,
            early_stop_generations=args.early_stop,
            tournament_size=args.tournament_size,
            adaptive_mutation=args.adaptive_mutation,
            apply_pruning=apply_pruning_in_ga,
            pruning_config=pruning_config,
        )
        
        print(f"GA search complete. Total results: {len(results)}")
        
        # Save best solution to JSON file
        if best_solution is not None:
            import json
            opt_type = optimize_metric if optimize_metric else 'None'
            sweep_type = component_type_to_sweep_type(component_types)
            best_file = f'{data_dir}/{desired_application}_{sweep_type}_{opt_type}_best_solution_ga.json'
            
            # Convert to JSON-serializable format
            best_json = {
                'application': desired_application,
                'metric': best_solution.get('metric'),
                'sweep_type': sweep_type,
                'search_strategy': 'ga',
                'objective_value': best_solution.get('objective_value'),
                'objective_unit': best_solution.get('objective_unit', ''),
                'fitness': best_solution.get('fitness'),
                'params': best_solution.get('params'),
            }
            with open(best_file, 'w') as f:
                json.dump(best_json, f, indent=2)
            print(f'Saved best solution to {best_file}')
        
        graph = [r for r in results if r is not None]
        print(f'Number of valid points: {len(graph)}')
        
        if save_graph:
            import pickle
            opt_type = optimize_metric if optimize_metric else 'None'
            output_file = f'{data_dir}/GA_all_workloads_{component_types}_{opt_type}_graph.pkl'
            with open(output_file, 'wb') as f:
                pickle.dump(graph, f)
            print(f'Saved graph data to {output_file}')
    else:
        # Original per-workload loop for exhaustive/pruning or single-workload GA
        # Track global best across all workloads for exhaustive/pruning
        global_best_solution = None
        global_best_fitness = -1e9
        global_best_workload = None
        global_best_component_files = None
        global_best_electrodes = None
        global_best_charge_times = None
        
        for workload in valid_workloads:
            print(f'Running simulation for workload: {workload}')
            electrodes = select_electrodes(desired_application, component_types, optimize_metric)
            charge_times = select_charge_times(component_types, optimize_metric)
            component_files = load_component_files(component_types, desired_application, workload)

            tasks = [(cf, e, ct) for cf in component_files for e in electrodes for ct in charge_times]

            print(f'Running simulations for {len(tasks)} tasks with component type: {component_types}, optimize_metric: {optimize_metric}, search: {search_strategy}')

            results = []
            best_solution = None
            if search_strategy == 'ga':
                results, best_solution = ga_search(
                    component_files,
                    electrodes,
                    charge_times,
                    optimize_metric,
                    workloads=None,  # Single workload mode
                    pop_size=args.pop_size,
                    generations=args.generations,
                    crossover_rate=args.crossover,
                    mutation_rate=args.mutation,
                    elitism=args.elitism,
                    early_stop_generations=args.early_stop,
                    tournament_size=args.tournament_size,
                    adaptive_mutation=args.adaptive_mutation,
                    apply_pruning=apply_pruning_in_ga,
                    pruning_config=pruning_config,
                )
                
                # Save best solution to JSON file
                if best_solution is not None:
                    import json
                    opt_type = optimize_metric if optimize_metric else 'None'
                    best_file = f'{data_dir}/{workload}_best_solution_{opt_type}.json'
                    best_json = {
                        'application': desired_application,
                        'workload': workload,
                        'metric': best_solution.get('metric'),
                        'objective_value': best_solution.get('objective_value'),
                        'objective_unit': best_solution.get('objective_unit', ''),
                        'fitness': best_solution.get('fitness'),
                        'params': best_solution.get('params'),
                    }
                    with open(best_file, 'w') as f:
                        json.dump(best_json, f, indent=2)
                    print(f'Saved best solution to {best_file}')
            elif search_strategy == 'pruning':
                # Use smart pruning with branch-and-bound optimization
                results, pruner = smart_pruning_search(
                    tasks, 
                    run_sim_silent, 
                    optimize_metric, 
                    pruning_config,
                    component_type=component_types
                )
                
                # Print pruning statistics
                stats = pruner.get_stats()
                print(f"  Smart Pruning Stats:")
                print(f"    Total tasks: {stats['total_tasks']}")
                print(f"    Evaluated: {stats['evaluated_tasks']}")
                print(f"    Pruned: {stats['pruned_tasks']} ({stats['prune_rate']})")
                print(f"    Valid results: {stats['valid_results']}")
                if stats.get('electrode_thresholds'):
                    print(f"    Electrode thresholds: {stats['electrode_thresholds']}")
                if stats.get('power_thresholds'):
                    print(f"    Power thresholds: {stats['power_thresholds']}")
                if stats.get('processor_thresholds'):
                    print(f"    Processor implant kernel thresholds: {stats['processor_thresholds']}")
            else:
                if component_types == 'processor':
                    for task in tqdm(tasks, desc="Simulating"):
                        res = run_sim_silent(task)
                        if res is not None:
                            results.append(res)
                else:
                    with Pool(cpu_count()) as pool:
                        for res in tqdm(pool.imap_unordered(run_sim_silent, tasks), total=len(tasks), desc="Simulating"):
                            if res is not None:
                                results.append(res)

            print(f"length of results: {len(results)}")

            graph = [r for r in results if r is not None]
            
            # Extract best solution for exhaustive/pruning searches (GA already does this)
            # Track the global best across all workloads instead of saving per-workload
            if search_strategy != 'ga' and graph:
                best_solution = None
                best_fitness = -1e9
                
                # Find the best result based on the metric for this workload
                for res in graph:
                    fit = fitness(res, optimize_metric)
                    if fit > best_fitness:
                        best_fitness = fit
                        best_solution = res
                
                # Update global best if this workload has better solution
                if best_solution is not None and best_fitness > global_best_fitness:
                    global_best_fitness = best_fitness
                    global_best_solution = best_solution
                    global_best_workload = workload
                    global_best_component_files = component_files
                    global_best_electrodes = electrodes
                    global_best_charge_times = charge_times
            
            print(f'Number of points in the graph: {len(graph)}')
            # Save the graph to a pickle file
            import pickle
            
            if component_types == 'processor':
                opt_type = 'None'
            else:
                opt_type = optimize_metric if optimize_metric else 'None'

            if save_graph:
                with open(f'{data_dir}/{workload}_{component_types}_{opt_type}_graph.pkl', 'wb') as f:
                    pickle.dump(graph, f)
                print(f'Saved graph data to {data_dir}/{workload}_{component_types}_{opt_type}_graph.pkl')
            else:
                print('Skipping graph save (disabled)')
            # close the plot
            # plt.close()
        
        # After all workloads processed, save the global best solution for exhaustive/pruning
        if search_strategy != 'ga' and global_best_solution is not None:
            import json
            opt_type = optimize_metric if optimize_metric else 'None'
            sweep_type = component_type_to_sweep_type(component_types)
            best_file = f'{data_dir}/{desired_application}_{sweep_type}_{opt_type}_best_solution_{search_strategy}.json'
            
            # Extract objective value from the global best result
            objective_value = None
            objective_unit = ''
            
            # Handle different result tuple formats
            if component_types == 'processor':
                # Processor result: (offloading_score, num_elec, peak_power_violation, avg_power_violation, latency_violation)
                if len(global_best_solution) >= 5:
                    offloading_score, num_elec, peak_power_v, avg_power_v, latency_v = global_best_solution[:5]
                    objective_value = offloading_score
                    objective_unit = 'offloading_score'
            elif len(global_best_solution) >= 9:
                # TRX-like result: (BER, num_elec, peak_power_v, avg_power_v, latency_v, component_file, lifetime, latency, implant_power, ...)
                _, num_elec, _, _, _, _, lifetime, latency, implant_power, *_ = global_best_solution
                if optimize_metric == 'operatingtime':
                    objective_value = lifetime
                    objective_unit = 'hours'
                elif optimize_metric == 'throughput':
                    objective_value = num_elec
                    objective_unit = 'electrodes'
                elif optimize_metric == 'latency':
                    objective_value = latency
                    objective_unit = 'ms'
                elif optimize_metric == 'implant_power':
                    objective_value = implant_power
                    objective_unit = 'mW'
            
            params = extract_params_from_result(global_best_solution, global_best_component_files, 
                                                 global_best_electrodes, global_best_charge_times, workloads=None)
            # Add workload to params
            params['workload'] = global_best_workload
            
            best_json = {
                'application': desired_application,
                'workload': global_best_workload,
                'metric': optimize_metric,
                'sweep_type': sweep_type,
                'search_strategy': search_strategy,
                'objective_value': objective_value,
                'objective_unit': objective_unit,
                'fitness': global_best_fitness,
                'params': params,
            }
            with open(best_file, 'w') as f:
                json.dump(best_json, f, indent=2)
            print(f'\n=== Global best solution across all {len(valid_workloads)} workloads ===')
            print(f'Best workload: {global_best_workload}')
            print(f'Objective: {objective_value} {objective_unit}')
            print(f'Fitness: {global_best_fitness}')
            print(f'Saved best solution to {best_file}')