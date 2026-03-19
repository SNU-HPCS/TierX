import yaml, sys
import numpy as np
sys.path.append('..')
from profiler import ADD_STATS, UPDATE_STATS

class Sensor():
    def __init__(self, node):
        self.node = node
        args = node.args['sensor']
        
        self.electrodes = args['electrodes']
        self.sampling_rate = args['sampling_rate']
        self.bit_precision = args['bit_precision']
        self.data_rate = self.electrodes * self.sampling_rate * self.bit_precision * 1e-3

        # Base values before scaling
        base_static_power = args['static_power']
        base_dynamic_power = args['dynamic_power']
        
        # Get input scaling configuration
        input_scaling = node.input_scaling
        scaling_enabled = input_scaling.get('enabled', False)
        
        # Apply input-dependent scaling if enabled
        if scaling_enabled:
            baseline_electrodes = input_scaling.get('baseline_electrodes', 100)
            current_electrodes = self.electrodes
            scaling_ratio = current_electrodes / baseline_electrodes if baseline_electrodes > 0 else 1.0
            
            sensor_scaling = input_scaling.get('sensor', {})
            dynamic_power_exp = sensor_scaling.get('dynamic_power_exponent', 1.0)
            static_power_exp = sensor_scaling.get('static_power_exponent', 0.0)
            
            # Apply scaling: scaled_value = base_value * (ratio ^ exponent)
            self.static_power = base_static_power * (scaling_ratio ** static_power_exp)
            self.dynamic_power = base_dynamic_power * (scaling_ratio ** dynamic_power_exp)
        else:
            self.static_power = base_static_power
            self.dynamic_power = base_dynamic_power

    def sense(self, simulation_end, trial_duration, trial_period, required_latency):
        data_blocks = []
        # Add sensing schedules to node
        for start_time in np.arange(0, simulation_end, trial_period):
            self.node.pmu.discharge_power(self.dynamic_power, start_time, trial_duration, 'sensor', initial=True)
            ADD_STATS(self.node, start_time, trial_duration, 'sense')

            data = self.node.DataBlock()
            data.spatial_samples = self.electrodes
            data.start_time = start_time
            data.duration = trial_duration
            data.bit_precision = self.bit_precision
            data.sampling_rate = self.sampling_rate
            data.size = data.spatial_samples * data.duration * data.sampling_rate * data.bit_precision
            data.input_from = 'sensor'
            data_blocks.append([data])

            if trial_duration < trial_period:
                # Add idle stats
                idle_start_time = start_time + trial_duration
                idle_duration = trial_period - trial_duration
                self.node.pmu.discharge_power(0, idle_start_time, idle_duration, 'sensor', initial=True)
                ADD_STATS(self.node, idle_start_time, idle_duration, 'idle')
        
        # Wait for all data blocks to be processed
        self.node.pmu.discharge_power(0, simulation_end, required_latency, 'sensor', initial=True)
        ADD_STATS(self.node, simulation_end, required_latency, 'idle')
            
        return data_blocks