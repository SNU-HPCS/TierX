import yaml
from . import powerManagement, communicationLink, sensor, processor
import numpy as np
import sys
sys.path.append('..')
from profiler import ADD_STATS, UPDATE_STATS

class Node():
    def __init__(self, args, name, node_id):
        self.args = args
        self.id = node_id
        self.name = name
        self.location = args['location']
        self.schedule = []
        self.data_blocks = []
        self.power_blocks = []
        self.dynamic_power = 0
        self.required_voltage = args['required_voltage']
        self.required_SAR = args['required_SAR']
        
        # Get power constraints from args (loaded from TierX.yaml) with fallback defaults
        power_constraints = args.get('power_constraints', {})
        
        # Get input scaling configuration (loaded from TierX.yaml)
        self.input_scaling = args.get('input_scaling', {
            'enabled': False,
            'baseline_electrodes': 100,
            'processor': {'latency_exponent': 1.0, 'dynamic_power_exponent': 1.0, 'static_power_exponent': 0.0},
            'sensor': {'dynamic_power_exponent': 1.0, 'static_power_exponent': 0.0}
        })
        
        # Get pipelining configuration (loaded from TierX.yaml)
        self.pipelining = args.get('pipelining', {
            'compute_comm': {'enabled': False, 'mode': 'sequential', 'overlap_ratio': 0.0},
            'comm_compute': {'enabled': False, 'mode': 'sequential', 'overlap_ratio': 0.0}
        })
        
        if name == 'implant_0': # on-implant node
            self.type = 'implant'
            self.sensor = sensor.Sensor(self)
            self.pmu = powerManagement.PowerManagement(self)
            self.processor = processor.Processor(self)
            self.comm_link = communicationLink.CommunicationLink(self)

            implant_constraints = power_constraints.get('implant', {})
            self.power_constraint = implant_constraints.get('peak', 15)  # in mW (peak power constraint)
            self.avg_power_constraint = implant_constraints.get('average', 10)  # in mW (average power constraint)
        else: # near-implant or off-implant node
            if 'external' in self.location: # if external node
                self.type = 'external'
                external_constraints = power_constraints.get('external', {})
                self.power_constraint = external_constraints.get('peak', 0)
                self.avg_power_constraint = external_constraints.get('average', 0)
            elif 'off_body' not in self.location: # if near-implant node
                self.type = 'onbody'
                onbody_constraints = power_constraints.get('onbody', {})
                self.power_constraint = onbody_constraints.get('peak', 200)  # in mW (peak power constraint)
                self.avg_power_constraint = onbody_constraints.get('average', 100)  # in mW (average power constraint)
            else:
                assert 0, "Invalid node type or location"
            self.pmu = powerManagement.PowerManagement(self)
            self.processor = processor.Processor(self)
            self.comm_link = communicationLink.CommunicationLink(self)
        
        if self.type == 'implant':
            self.static_power = self.sensor.static_power + self.processor.static_power + self.comm_link.static_power + self.pmu.static_power
            self.static_power_breakdown = {
                'sensor': self.sensor.static_power, 
                'processor': self.processor.static_power, 
                'comm_link': self.comm_link.static_power, 
                'pmu': self.pmu.static_power, 
                'charge_loss': self.pmu.charge_loss,
                'sram': self.processor.sram_static_power,  # SRAM static power (included in processor)
            }
            # Store SRAM specs for reporting
            self.sram_specs = self.processor.sram_specs
        else:
            self.static_power = self.processor.static_power + self.comm_link.static_power + self.pmu.static_power
            self.static_power_breakdown = {
                'processor': self.processor.static_power, 
                'comm_link': self.comm_link.static_power, 
                'pmu': self.pmu.static_power, 
                'charge_loss': self.pmu.charge_loss,
                'sram': self.processor.sram_static_power,
            }
            self.sram_specs = self.processor.sram_specs
        
    class DataBlock():
        def __init__(self):
            self.type = 'data'
            self.spatial_samples = 0
            self.start_time = 0
            self.duration = 0
            self.bit_precision = 0
            self.sampling_rate = 0
            self.size = 0
            self.input_from = ''

            self.errors = 0
            self.compressed = False
            self.redundancy = 0

            # Wireless parameters
            self.wireless = False
            self.method = ''
            self.modulation = ''
            self.signal_power = 0
            self.frequency = 0
            self.bandwidth = 0
            self.data_rate = 0
            self.rate = 0

    def sense(self, simulation_end, trial_duration, trial_period, required_latency):
        self.data_blocks = self.sensor.sense(simulation_end, trial_duration, trial_period, required_latency)
        return
    
    def compute(self, num_trials):
        self.data_blocks = self.processor.compute(num_trials)
        return self.data_blocks
    
    def transmit_data(self, dst_node):
        # Transmit data wirelessly
        self.data_blocks = self.comm_link.transmit_data(dst_node)
        return

    def receive_data(self, src_node, prop_latency):
        # Receive wireless data
        self.data_blocks, BER, retransmission_num = self.comm_link.receive_data(src_node, prop_latency)
        return BER, retransmission_num
    
    def transmit_power(self, power, dst_node, start_time, duration):
        # Transmit power wirelessly
        self.power_blocks = self.pmu.transmit_power(power, dst_node, start_time, duration)
        return

    def receive_power(self, power, src_node, prop_latency, start_time, duration):
        # Receive wireless power
        self.power_blocks = self.pmu.receive_power(power, src_node, prop_latency, start_time, duration)
        return
    
    def check_voltage(self, voltage):
        # Check if the voltage is within the required range
        if voltage < self.required_voltage:
            return False
        return True
    
