import yaml, sys
sys.path.append('..')
from profiler import ADD_STATS, UPDATE_STATS
import numpy as np
from . import sram_model

class Processor():
    def __init__(self, node):
        self.node = node
        args = node.args['processor']
        
        # Get input scaling configuration
        input_scaling = node.input_scaling
        scaling_enabled = input_scaling.get('enabled', False)
        
        # Get SRAM model configuration
        sram_config = node.args.get('sram_model', {'enabled': False})
        self.sram = sram_model.SRAMModel(sram_config)
        
        if node.type == 'implant':
            self.output_offset = args['output_offset']
            self.output_latency = args['output_latency']
            self.output_stride = args['output_stride']
            self.input_timesteps_per_output = args['input_timesteps_per_output']
            self.output_spatial = args['output_spatial']
            self.output_temporal = args['output_temporal']
            self.output_bit_precision = args['output_bit_precision']
        else:
            self.output_latency = args['output_latency']
            self.output_stride = args['output_stride']
            self.input_timesteps_per_output = args['input_timesteps_per_output']
            self.output_spatial = args['output_spatial']
            self.output_temporal = args['output_temporal']
            self.output_bit_precision = args['output_bit_precision']
        
        # Base values before scaling
        base_static_power = args['static_power']
        base_dynamic_power = args['dynamic_power']
        base_output_latency = self.output_latency
        
        # Get electrode count for SRAM sizing
        if node.type == 'implant':
            if hasattr(node, 'sensor'):
                current_electrodes = node.sensor.electrodes
                bit_precision = node.sensor.bit_precision
                sampling_rate = node.sensor.sampling_rate
            elif 'sensor' in node.args:
                current_electrodes = node.args['sensor'].get('electrodes', 100)
                bit_precision = node.args['sensor'].get('bit_precision', 16)
                sampling_rate = node.args['sensor'].get('sampling_rate', 30000)
            else:
                current_electrodes = 100
                bit_precision = 16
                sampling_rate = 30000
        else:
            current_electrodes = 100
            bit_precision = 16
            sampling_rate = 30000
        
        # Get kernel-related parameters first (needed for SRAM sizing)
        self.num_kernels = args.get('num_kernels', 0)
        if self.num_kernels > 0:
            self.kernel_strides = args.get('kernel_strides', [])
            self.kernel_dimensions = args.get('kernel_dimensions', [])
            self.kernel_latencies = args.get('kernel_latencies', [])
            self.kernel_powers = args.get('kernel_powers', [])
            
            # Get per-kernel SRAM access configuration (reads, writes per kernel execution)
            # Format: [[reads1, writes1], [reads2, writes2], ...] or None for defaults
            self.kernel_sram_accesses = args.get('kernel_sram_accesses', None)
            
            # Set kernel info for SRAM intermediate buffer calculation (if dimensions are available)
            if self.kernel_dimensions:
                self.sram.set_kernel_info(self.num_kernels, self.kernel_dimensions, self.kernel_sram_accesses)
        else:
            self.kernel_strides = []
            self.kernel_dimensions = []
            self.kernel_latencies = []
            self.kernel_powers = []
            self.kernel_sram_accesses = []
        
        # ESP PLM override: when ESP-enabled PEs provide known PLM sizes,
        # use them as a buffer size floor in the SRAM model.
        esp_plm_overrides = args.get('esp_plm_overrides', None)
        if esp_plm_overrides:
            self.sram.set_esp_plm_overrides(esp_plm_overrides)
        
        # Calculate SRAM requirements based on electrode count and kernel structure
        self.sram_specs = self.sram.calculate_sram_requirements(
            electrodes=current_electrodes,
            bit_precision=bit_precision,
            sampling_rate=sampling_rate
        )
        
        # SRAM power contribution
        self.sram_static_power = self.sram_specs['static_power_mw']
        self.sram_dynamic_power = self.sram_specs['dynamic_power_mw']
        
        # Apply input-dependent scaling if enabled (only for implant nodes with sensor)
        if scaling_enabled and node.type == 'implant':
            baseline_electrodes = input_scaling.get('baseline_electrodes', 100)
            
            scaling_ratio = current_electrodes / baseline_electrodes if baseline_electrodes > 0 else 1.0
            
            processor_scaling = input_scaling.get('processor', {})
            latency_exp = processor_scaling.get('latency_exponent', 1.0)
            dynamic_power_exp = processor_scaling.get('dynamic_power_exponent', 1.0)
            static_power_exp = processor_scaling.get('static_power_exponent', 0.0)
            
            # Apply scaling: scaled_value = base_value * (ratio ^ exponent)
            self.output_latency = base_output_latency * (scaling_ratio ** latency_exp)
            self.static_power = base_static_power * (scaling_ratio ** static_power_exp)
            self.dynamic_power = base_dynamic_power * (scaling_ratio ** dynamic_power_exp)
        else:
            self.static_power = base_static_power
            self.dynamic_power = base_dynamic_power
        
        # Add SRAM power to processor power
        self.static_power += self.sram_static_power
        self.dynamic_power += self.sram_dynamic_power
        self.sample_count = 0
        self.compute_start_time = 0
        self.sampling_rate = 30
        

    
    def compute(self, num_trials):
        print("Processing data of node:", self.node.name)
        # start_times = 2D list of start times for each data block
        new_data_blocks = [[] for _ in range(num_trials)]
        # Iterate over all data blocks
        for idx, trial in enumerate(self.node.data_blocks):
            # trial = list of data blocks
            for block_idx, data in enumerate(trial):
                # print(f'Processing data block {block_idx+1} of trial {idx+1}')
                if data.input_from == 'sensor':
                    if block_idx > 0:
                        print('Exit: Sensor data block should be the one per trial')
                        sys.exit()
                    start_time = data.start_time
                    sampling_rate = data.sampling_rate
                    block_timesteps = int(data.duration * sampling_rate)
                    print(f'Processing sensor data block with {block_timesteps} timesteps')

                    for stride in range(0, block_timesteps, self.output_stride):
                        if stride + self.input_timesteps_per_output > block_timesteps:
                            break
                        if self.num_kernels > 0:
                            # offset for pipeline fill
                            offset_time = self.kernel_strides[0] / sampling_rate + stride / sampling_rate
                            compute_latency = self.output_latency + self.output_offset - self.kernel_strides[0] / sampling_rate
                        else:
                            offset_time = stride / sampling_rate
                            compute_latency = self.output_latency
                        compute_start_time = start_time + offset_time

                        data = self.node.DataBlock()
                        data.spatial_samples = self.output_spatial
                        data.bit_precision = self.output_bit_precision
                        data.size = data.spatial_samples * self.output_temporal * data.bit_precision
                        # data.start_time = compute_start_time
                        data.start_time = start_time + self.output_offset + stride / sampling_rate
                        data.duration = self.output_latency
                        # data.rate = sampling_rate
                        new_data_blocks[idx].append(data)
                        print(f'New data block created with start time: {data.start_time}, duration: {data.duration}, size: {data.size}')
                        
                        # sys.__stdout__.write(f'Compute start time: {compute_start_time}, Output latency: {compute_start_time + self.output_latency}\n')
                        
                        self.node.pmu.discharge_power(self.dynamic_power, compute_start_time, compute_latency*1.01, 'processor')
                        UPDATE_STATS(self.node, compute_start_time, compute_latency*1.01, 'compute')

                else:
                    start_time = data.start_time + data.duration
                    self.output_offset = data.duration
                    
                    compute_start_time = start_time
                    compute_latency = self.output_latency
                    self.node.pmu.discharge_power(self.dynamic_power, compute_start_time, compute_latency*1.01, 'processor')
                    UPDATE_STATS(self.node, compute_start_time, compute_latency*1.01, 'compute')

                    data = self.node.DataBlock()
                    data.spatial_samples = self.output_spatial
                    data.bit_precision = self.output_bit_precision
                    data.size = data.spatial_samples * self.output_temporal * data.bit_precision
                    # data.start_time = compute_start_time
                    data.start_time = start_time
                    data.duration = self.output_latency
                    new_data_blocks[idx].append(data)
        return new_data_blocks