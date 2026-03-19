"""Create hardware component YAML files for TierX simulation.

This script generates YAML files with various hardware configurations.
"""

import yaml
import os
from itertools import product
import sys


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


search_space = load_search_space()


def load_tierx_config(path: str = None):
    """Load TierX.yaml config if present (used to align component subsets with run config)."""
    if path is None:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        path = os.path.join(base_dir, 'TierX.yaml')
    if os.path.exists(path):
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    return {}


tierx_cfg = load_tierx_config()

# Reconfigure for the subset of processor/transceiver hardware from SearchSpace.yaml
component_cfg = search_space.get('component_generation', {}) if isinstance(search_space, dict) else {}
trx_cfg = component_cfg.get('trx', {}) if isinstance(component_cfg, dict) else {}
processor_cfg = component_cfg.get('processor', {}) if isinstance(component_cfg, dict) else {}
power_cfg = component_cfg.get('power', {}) if isinstance(component_cfg, dict) else {}

create_subset_processor = processor_cfg.get('create_subset', False)
create_subset_trx = trx_cfg.get('create_subset', True)

desired_trx_hw_cfg = trx_cfg.get('desired') if isinstance(trx_cfg, dict) else None
if desired_trx_hw_cfg:
    desired_trx_hw = {
        (
            entry.get('method'),
            float(entry.get('data_rate')),
            float(entry.get('dynamic_power')),
            entry.get('modulation'),
            entry.get('radiated_power'),
            entry.get('bandwidth'),
        )
        for entry in desired_trx_hw_cfg
    }
else:
    desired_trx_hw = {('RF', 20.0, 4.0, 'BPSK', 10, 2), ('BCC', 80.0, 6.4, 'BPSK', 1, 40)}

# get component types from the command line argument, if not provided, assert error
# check if the component types are in the predefined list
predefined_application_types = ['NN', 'Seizure', 'SpikeSorting', 'GRU']
predefined_component_types = ['trx', 'processor', 'power', 'env']
if len(sys.argv) > 2:
    desired_application = sys.argv[1]
    assert desired_application in predefined_application_types, f'Invalid application: {desired_application}. ' \
                                                     f'Predefined applications are: {predefined_application_types}'
    component_types = sys.argv[2]
    assert component_types in predefined_component_types, f'Invalid component type: {component_types}. ' \
                                                         f'Predefined types are: {predefined_component_types}'
else:
    # If no component type is provided, assert error and print the predefined types
    raise ValueError(f'Component type is not provided. Predefined types are: {predefined_component_types}')

# Remove all existing files in the directory
if component_types in ['processor']:
    component_path = f'lib/Input/HW_components/{component_types}/{desired_application}'
else:
    component_path = f'lib/Input/HW_components/{component_types}'

if not os.path.exists(component_path):
    os.makedirs(component_path)
else:
    for file in os.listdir(component_path):
        file_path = os.path.join(component_path, file)
        if os.path.isfile(file_path):
            os.remove(file_path)

if component_types == 'trx':
    # If using a subset, ensure it includes the communication_methods requested in TierX.yaml.
    # This avoids a mismatch where TierX workloads reference e.g. LOW-BCC but the subset omits it.
    tierx_comm_methods = tierx_cfg.get('communication_methods', []) if isinstance(tierx_cfg, dict) else []
    if isinstance(tierx_comm_methods, list):
        tierx_comm_methods = [str(x) for x in tierx_comm_methods]
    else:
        tierx_comm_methods = []

    comm_to_tuple = {
        # Mirrors src/generate_all_configs.py hardcoded transceiver configs
        'LOW-RF': ('RF', 20.0, 4.0, 'BPSK', 10, 2),
        'LOW-BCC': ('BCC', 20.0, 1.6, 'BPSK', 1, 40),
        'HIGH-BCC': ('BCC', 80.0, 6.4, 'BPSK', 1, 40),
    }
    for comm in tierx_comm_methods:
        tup = comm_to_tuple.get(comm)
        if tup:
            desired_trx_hw.add(tup)

    transceiver_methods = trx_cfg.get('methods', ['RF', 'BCC'])
    transceiver_frequencies = trx_cfg.get('frequencies', [900, 40])  # in MHz
    transceiver_bandwidths = trx_cfg.get('bandwidths', [2, 40])  # in MHz
    transceiver_data_rates = trx_cfg.get('data_rates', list(range(10, 90, 10)))  # in Mbps
    transceiver_modulations = trx_cfg.get('modulations', ['BPSK', 'OOK'])
    transceiver_radiated_powers = trx_cfg.get('radiated_powers', [1, 10])  # in mW
    transceiver_static_powers = trx_cfg.get('static_powers', [0])  # in mW
    transceiver_dynamic_powers = trx_cfg.get('dynamic_powers', [1.6, 4.0, 6.4])  # in mW
    transceiver_noise_figures = trx_cfg.get('noise_figures', [10])  # in dB
    # Create a for loop to generate yaml files for each combination of parameters
    for method, frequency, bandwidth, data_rate, modulation, static_power, dynamic_power, radiated_power, noise_figure in \
            product(transceiver_methods, transceiver_frequencies, transceiver_bandwidths,
                    transceiver_data_rates, transceiver_modulations,
                    transceiver_static_powers, transceiver_dynamic_powers, transceiver_radiated_powers, transceiver_noise_figures):
        
        # Additional contidions for realistic parameters
        if 'RF' in method:
            if frequency not in [400, 900, 2400, 5000]:
                continue
        elif 'BCC' in method:
            if frequency not in [10, 40, 100]:
                continue

        # Sanity check
        # Ensure energy per bit is within reasonable range (10 pJ/b to 300 pJ/b)
        if dynamic_power / data_rate * 1000 < 10 or dynamic_power / data_rate * 1000 > 300: # in pJ/b
            continue


        if create_subset_trx:
            # Check if the current combination is in the desired set
            if (method, data_rate, dynamic_power, modulation, radiated_power, bandwidth) not in desired_trx_hw:
                continue
        
        # Create a dictionary for the transceiver spec
        transceiver_spec = {
            'type': 'TRX',
            'method': method,
            'frequency': frequency,
            'bandwidth': bandwidth,
            'data_rate': data_rate,
            'modulation': modulation,
            'radiated_power': radiated_power,
            'static_power': static_power,
            'dynamic_power': dynamic_power,
            'noise_figure': noise_figure
        }

        # Create yaml file name based on the parameters
        file_name = f'{method}_{frequency}MHz_{bandwidth}MHz_{data_rate}Mbps_{modulation}_{radiated_power}mW_{static_power}mW_{dynamic_power}mW_{noise_figure}dB.yaml'
        file_path = os.path.join(component_path, file_name)

        print(f'Creating file: {file_path}')
        # Create yaml file with the transceiver spec
        with open(file_path, 'w') as f:
            yaml.dump({'transceivers': [transceiver_spec]}, f)
        
        print(f'File {file_path} created successfully.')

elif component_types == 'power':
    # Default: mirror lib/Input/Common_power_transfer_units.yaml.
    # Optional: add component_generation.power.energy_densities in SearchSpace.yaml to sweep storage energy density.
    common_path = 'lib/Input/Common_power_transfer_units.yaml'
    if os.path.exists(common_path):
        with open(common_path, 'r') as f:
            spec = yaml.safe_load(f) or {}
    else:
        spec = {
            'power_management': {
                'transceivers': []
            }
        }

    energy_densities_by_source = None
    energy_densities_flat = None
    if isinstance(power_cfg, dict):
        energy_densities_by_source = power_cfg.get('energy_densities_by_source')
        energy_densities_flat = power_cfg.get('energy_densities')

    # Always write a baseline default.yaml for safety/fallback.
    default_path = os.path.join(component_path, 'default.yaml')
    with open(default_path, 'w') as f:
        yaml.dump(spec, f)
    print(f'Created power component: {default_path}')

    def _write_variant(source: str, ed):
        ed_token = str(ed).replace('.', 'p')
        if source:
            file_path = os.path.join(component_path, f'{source}_energy_density_{ed_token}.yaml')
        else:
            file_path = os.path.join(component_path, f'energy_density_{ed_token}.yaml')
        out = dict(spec) if isinstance(spec, dict) else {}
        if not isinstance(out.get('power_management'), dict):
            out['power_management'] = {}
        out['power_management'] = dict(out['power_management'])
        out['power_management']['energy_storage'] = {'energy_density': ed}
        with open(file_path, 'w') as f:
            yaml.dump(out, f)
        print(f'Created power component: {file_path}')

    wrote_any = False
    if isinstance(energy_densities_by_source, dict) and len(energy_densities_by_source) > 0:
        for source, values in energy_densities_by_source.items():
            if not isinstance(values, list) or len(values) == 0:
                continue
            for ed in values:
                _write_variant(str(source), ed)
                wrote_any = True

    if (not wrote_any) and isinstance(energy_densities_flat, list) and len(energy_densities_flat) > 0:
        for ed in energy_densities_flat:
            _write_variant('', ed)
            wrote_any = True

    # If no sweep values configured, default.yaml is the only file.

elif component_types == 'env':
    # Default: no override (use workload environment as-is).
    file_path = os.path.join(component_path, 'default.yaml')
    with open(file_path, 'w') as f:
        yaml.dump({'environment': {}}, f)
    print(f'Created environment component: {file_path}')

elif component_types == 'processor':
    def load_pe_file(filepath):
        with open(filepath, 'r') as f:
            return yaml.safe_load(f)

    def all_3_way_splits(pipeline):
        n = len(pipeline)
        splits = []
        for i in range(0, n+1):
            for j in range(i, n+1):
                splits.append([pipeline[:i], pipeline[i:j], pipeline[j:]])
        return splits

    def compute_processor_entry(pe_names, pe_db, sram_accesses_list, off=False):
        # Application-specific base configurations: (output_offset, output_stride, input_timesteps)
        APP_PROC_CONFIG = {
            'NN': (150, 1500, 4500),
            'Seizure': (4, 120, 120),
            'SpikeSorting': (4, 120, 120),
            'GRU': (100, 3000, 3000),
        }
        if desired_application not in APP_PROC_CONFIG:
            raise ValueError(f"Unknown application: {desired_application}")
        
        offset, stride, timesteps = APP_PROC_CONFIG[desired_application]
        entry = {
            'output_offset': offset,
            'output_latency': 0.0,
            'output_stride': stride,
            'input_timesteps_per_output': timesteps,
            'output_spatial': 0,
            'output_temporal': stride,
            'output_bit_precision': 32,
            'static_power': 0.0,
            'dynamic_power': 0.0
        }

        kernel_powers = []
        kernel_latencies = []
        kernel_sram_accesses = []
        esp_plm_overrides = []  # Collect PLM overrides from ESP-enabled PEs
        for idx, pe in enumerate(pe_names):
            pe_info = pe_db.get(pe)
            if not pe_info:
                raise ValueError(f"PE '{pe}' not found in PEs section")

            latency = pe_info.get('latency_ms') or 0.0
            static = pe_info['power'].get('static') or 0.0
            dynamic = pe_info['power'].get('dynamic') or 0.0

            # --- ESP-enabled PE handling ---
            esp_config = pe_info.get('esp', {})
            esp_enabled = esp_config.get('enabled', False)
            if esp_enabled:
                # Validate: warn if power values are not populated from synthesis
                if static == 0.0 and dynamic == 0.0:
                    print(f"  WARNING: PE '{pe}' has esp.enabled=true but power is 0.0")
                    print(f"           Run: python3 src/esp_report_parser.py "
                          f"--docker <container> --accel {esp_config.get('source', pe)} --update-pe {pe}")

                # Apply batching_factor to latency
                batching = esp_config.get('batching_factor', 1)
                if batching > 1:
                    latency *= batching

                # Collect PLM sizes for SRAM override
                chunk = esp_config.get('chunk_factor', {})
                data_bw = esp_config.get('data_bitwidth', 32)
                in_place = esp_config.get('in_place', False)
                plm_input_words = chunk.get('input', 0) if isinstance(chunk, dict) else 0
                plm_output_words = chunk.get('output', 0) if isinstance(chunk, dict) else 0
                if plm_input_words > 0:
                    plm_input_bytes = plm_input_words * (data_bw // 8)
                    plm_output_bytes = 0 if in_place else plm_output_words * (data_bw // 8)
                    esp_plm_overrides.append({
                        'kernel_idx': idx,
                        'pe': pe,
                        'plm_input_bytes': plm_input_bytes,
                        'plm_output_bytes': plm_output_bytes,
                        'in_place': in_place,
                    })

            # Get per-kernel SRAM access pattern from pipeline definition
            if idx < len(sram_accesses_list):
                sram_accesses = list(sram_accesses_list[idx])
            else:
                sram_accesses = [1, 1]  # default

            if off:
                latency *= 0.25
                static *= 4
                dynamic *= 4

            entry['output_latency'] += latency
            kernel_latencies.append(latency)
            entry['static_power'] += static / 1000  # µW to mW
            entry['dynamic_power'] += dynamic * 1024 / 1000 # uW to mW, scale to 1024 electrodes
            kernel_powers.append(dynamic*1024/1000)
            kernel_sram_accesses.append(sram_accesses)
            if desired_application == 'NN' and pe == 'BMUL':
                entry['output_spatial'] += 1  # Count the number of BMUL PEs, this value is later calculated in the simulation
        entry['kernel_latencies'] = kernel_latencies
        entry['kernel_powers'] = kernel_powers
        entry['kernel_sram_accesses'] = kernel_sram_accesses

        # Include ESP PLM overrides if any ESP-enabled PEs are present
        if esp_plm_overrides:
            entry['esp_plm_overrides'] = esp_plm_overrides

        return entry

    # Load PEs.yaml
    pe_yaml_path = 'lib/Input/PEs.yaml'
    if not os.path.exists(pe_yaml_path):
        raise FileNotFoundError(f"Missing required file: {pe_yaml_path}")
    
    pe_data = load_pe_file(pe_yaml_path)
    application_list = pe_data.get('Application', [])
    application = None
    for app in application_list:
        if app['name'] == desired_application:
            application = app
            break
    if not application:
        raise ValueError(f"Application '{desired_application}' not found in PEs.yaml")

    pipeline = application.get('pipeline', [])
    sram_accesses = application.get('sram_accesses', [(1,1)] * len(pipeline))  # Default: (1,1) per kernel
    pe_db = pe_data.get('PEs', {})

    all_splits = all_3_way_splits(pipeline)
    # Also split sram_accesses the same way as pipeline
    def split_sram_accesses(sram_list, split):
        """Split sram_accesses list according to pipeline split."""
        result = []
        offset = 0
        for segment in split:
            segment_len = len(segment)
            result.append(sram_list[offset:offset + segment_len])
            offset += segment_len
        return result

    # If only subset of splits is needed
    if create_subset_processor:
        subset = processor_cfg.get('subset_splits', [[5, 1, 1], [1, 5, 1], [1, 1, 5]])
        all_splits = [split for split in all_splits if [len(segment) for segment in split] in subset]

    print(f'Generating processor configurations for {len(all_splits)} splits of the pipeline: {pipeline}')
    print(f"total splits: {all_splits}")

    for idx, split in enumerate(all_splits):
        processors = []
        sram_split = split_sram_accesses(sram_accesses, split)
        for seg_idx, segment in enumerate(split):
            proc = compute_processor_entry(segment, pe_db, sram_split[seg_idx], off=(seg_idx==2))
            processors.append(proc)

        # 
        split_num_list = [len(segment) for segment in split]

        output_data = {
            'split': split_num_list,  # Always include, even with empty segments
            'processor': processors
        }

        file_path = os.path.join(component_path, f'processor_{split_num_list[0]}_{split_num_list[1]}_{split_num_list[2]}.yaml')
        with open(file_path, 'w') as f:
            yaml.dump(output_data, f)
        print(f'Created processor config: {file_path}')

