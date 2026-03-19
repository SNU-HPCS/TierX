import yaml, sys
import copy
import Node.util as util
sys.path.append('..')
from profiler import ADD_STATS, UPDATE_STATS, REPORT_VIOLATION

def SOC_to_voltage(SOC, V_max, V_min=0, type='linear'):
    # SOC: state of charge
    # V_max: maximum voltage
    # V_min: minimum voltage
    # V_max and V_min should be defined in the yaml file
    # V = V_min + (V_max - V_min) * SOC
    # return V

    if type == 'supercapacitor':
        # supercapacitor model (since Q=CV)
        return V_min + (V_max - V_min) * SOC
    elif type == 'battery':
        # Implement battery model here
        # reference: chrome-extension://efaidnbmnnnibpcajpcglclefindmkaj/https://resonetics.com/wp-content/uploads/2022/08/Contego-440-0421-Rescopy23.pdf
        # above 5% SOC, the voltage is linear
        if SOC >= 0.05:
            return 3.3 + (V_max - 3.3) * (SOC - 0.05) / 0.95
        else: # below 5% SOC, sharp drop in voltage
            return 3.0 + (3.3 - 3.0) * SOC / 0.05
    else:
        print(f'Exit: {type} type is not defined in SOC_to_voltage')
        sys.exit()


class PowerManagement():
    def __init__(self, node):
        self.node = node
        args = node.args['power_management']
        self.static_power = 0
        self.dynamic_power = 0
        self.charge_loss = 0

        # SAR constants for different power transfer methods (W/kg per mW)
        # SAR constants keyed by lowercase method name (W/kg per mW)
        self.SAR_constants = {
            'rf': 0.001,          # Far-field RF
            'inductive': 0.005,   # Near-field inductive coupling
            'bcp': 0.002,         # Body-coupled power (capacitive)
            'bcc': 0.002          # Body-channel communication
        }

        trx_list = [self.Transceiver(trx) for trx in args['transceivers']]
        self.tx, self.rx = [], []
        for trx in trx_list:
            if trx.type in {'TX', 'TRX'}:
                if trx.method != 'none':
                    self.static_power += trx.static_power
                    self.dynamic_power += trx.dynamic_power
                self.tx.append(trx)
            if trx.type in {'RX', 'TRX'}:
                self.rx.append(trx)
            if trx.type not in {'TX', 'RX', 'TRX'}:
                print('Exit: Transceiver type not recognized')
                sys.exit()
        self.energy_storage = self.EnergyStorage(self, args['energy_storage'])
        
        self.power_consumption = {}
        self.power_breakdown = {}
        self.power_charge = {}
        self.charge_time = 0
        return

    class Transceiver():
        def __init__(self, args):
            self.type = args['type']
            self.method = args['method']
            if self.method == 'wired' or self.method == 'none':
                return
            self.frequency = args['frequency']
            self.bandwidth = args['bandwidth']

            if self.type in {'TX', 'TRX'}:
                self.radiated_power = args['radiated_power']
                self.static_power = args['static_power']
                self.dynamic_power = args['dynamic_power']
            if self.type in {'RX', 'TRX'}:
                self.rectification_efficiency = args['rectification_efficiency']
            
        def transmit_power(self, power):
            # Transmit power wirelessly
            power.wireless = True
            power.method = self.method
            power.signal_power = self.radiated_power
            power.frequency = self.frequency
            power.bandwidth = self.bandwidth
            return
        
        def receive_power(self, power):
            # Receive wireless power
            power.wireless = False
            return

        def check_TRX_interference(self, other_trx_list):
            for other_trx in other_trx_list:
                # Check if two transceivers interfere with each other
                # 1) Check if they are on the same frequency
                my_freq_band = (self.frequency - self.bandwidth / 2, self.frequency + self.bandwidth / 2)
                other_freq_band = (other_trx.frequency - other_trx.bandwidth / 2, other_trx.frequency + other_trx.bandwidth / 2)
                if util.check_overlap(my_freq_band[0], my_freq_band[1], other_freq_band[0], other_freq_band[1]) and self.method == other_trx.method:
                    return True
            return False

        def check_TRX_match(self, other_trx):
            if self.method == other_trx.method and self.frequency == other_trx.frequency and self.bandwidth == other_trx.bandwidth:
                return True
            return False
    
    class EnergyStorage():
        def __init__(self, node, args):
            self.node = node
            self.type = args['type']
            if self.type in {'battery', 'supercapacitor'}:
                self.weight = args['weight']
                self.energy_density = args['energy_density']
                self.round_trip_efficiency = args['round_trip_efficiency']
                self.self_discharge_rate = args['self_discharge_rate']

                self.initial_energy_capacity = self.energy_density * self.weight * 1000 * 3600 # in mJ
                self.energy_capacity = self.energy_density * self.weight * 1000 * 3600 # in mJ
                # self.energy_status = {0: self.energy_capacity * (args['initial_charge'] / 100)}
                self.initial_charge = args['initial_charge']
                self.max_lifetime = args['max_lifetime'] # in hrs
                self.min_charge_status = args['min_charge_status']
                self.v_max = args['v_max']
                self.coulombic_efficiency = args['coulombic_efficiency']
            else: # wired power
                self.initial_energy_capacity = 0 
                self.energy_capacity = 0
                # self.energy_status = {0: 0}
            return
    
    def restrict_power_based_on_SAR(self, radiated_power, required_SAR, method='rf'):
        # SAR: specific absorption rate
        # radiated_power: radiated power in mW
        # k is a constant that depends on the tissue type, frequency, and method
        method_key = str(method).lower()
        k = self.SAR_constants.get(method_key, 0.001)
        # SAR is in W/kg
        SAR = k * radiated_power
        ratio = min(required_SAR / SAR, 1)
        # Limit the radiated power based on SAR
        restricted_power = ratio * radiated_power

        if SAR > required_SAR:
            print(f'Warning: SAR violation detected in node {self.node.name}')
            print(f'Method: {method}, Calculated SAR: {SAR} W/kg, Required SAR: {required_SAR} W/kg')
            # You may want to call REPORT_VIOLATION here if you have timing info

        return restricted_power

    def get_overlapping_SAR(self, start_time, end_time, current_power, method):
        """Calculate total SAR from all overlapping power transmissions at the receiver node."""
        total_SAR = 0
        method_key = str(method).lower()
        k = self.SAR_constants.get(method_key, 0.001)
        check_SAR = False
        
        # Add current transmission's SAR
        total_SAR += k * current_power
        
        # Check all other nodes in the network for overlapping power transmissions
        if hasattr(self.node, 'net') and self.node.net is not None:
            for node in self.node.net.nodes:
                if node.id == self.node.id:
                    continue
                
                # Check node's schedule for power transmission operations
                if hasattr(node, 'schedule'):
                    for node_log in node.schedule:
                        log_start, log_end, operation, _, _, _, _, interfering_power_trx_list, _, _ = node_log
                        
                        # Check if this is a power transmission that overlaps with our time window
                        if 'transmit power' in operation and util.check_overlap(start_time, end_time, log_start, log_end):
                            if interfering_power_trx_list is not None:
                                for power_trx in interfering_power_trx_list:
                                    # Get the method and radiated power from the transceiver
                                    tx_method = power_trx.method if hasattr(power_trx, 'method') else 'RF'
                                    tx_power = power_trx.radiated_power if hasattr(power_trx, 'radiated_power') else 0
                                    tx_k = self.SAR_constants.get(str(tx_method).lower(), 0.001)
                                    total_SAR += tx_k * tx_power
        
        return total_SAR, check_SAR

    def get_energy_status(self, timestep):
        # Returns the SOC at the given timestep
        # The total energy status should be bounded by the max capacity
        # consider cycle lifetime
        if self.energy_storage.initial_energy_capacity != 0:
            energy_stat = self.energy_storage.initial_energy_capacity * (self.energy_storage.initial_charge / 100)
        else:
            energy_stat = 0
        if timestep == 0:
            return energy_stat, 0
        
        intervals = []
        # Calculate using power consumption, timestep
        sorted_power_consumption_keys = sorted(self.power_consumption.keys())
        for interval in sorted_power_consumption_keys:
            if interval[0] < timestep and interval[1] < timestep:
                energy_stat -= self.power_consumption[interval] * (interval[1] - interval[0])
                energy_stat += self.power_charge[interval] * (interval[1] - interval[0])
                # energy_stat is bounded by the energy capacity
                energy_stat = min(energy_stat, self.energy_storage.energy_capacity)
                energy_stat = max(energy_stat, 0)
                if energy_stat == 0:
                    print(f'Energy status: {energy_stat}, Energy capacity: {self.energy_storage.energy_capacity}')
                    print(f'Exit: Energy depleted in node: {self.node.name}')
                    REPORT_VIOLATION(self.node, interval[0], interval[1], 'energy', energy_stat, self.energy_storage.energy_capacity)

                intervals.append(interval)
            elif interval[0] < timestep and interval[1] >= timestep:
                energy_stat -= self.power_consumption[interval] * (timestep - interval[0])
                energy_stat += self.power_charge[interval] * (timestep - interval[0])
                # energy_stat is bounded by the energy capacity
                energy_stat = min(energy_stat, self.energy_storage.energy_capacity)
                energy_stat = max(energy_stat, 0)
                if energy_stat == 0:
                    print(f'Energy status: {energy_stat}, Energy capacity: {self.energy_storage.energy_capacity}')
                    print(f'Exit: Energy depleted in node: {self.node.name}')
                    REPORT_VIOLATION(self.node, interval[0], timestep, 'energy', energy_stat, self.energy_storage.energy_capacity)

                intervals.append((interval[0], timestep))
                
        # Sort intervals
        intervals.sort(key=lambda x: (x[0], x[1]))
        # Check if intervals are continuous
        for i in range(len(intervals) - 1):
            if intervals[i][1] != intervals[i + 1][0]:
                print(f'Exit: Intervals are not continuous - {intervals[i]} and {intervals[i + 1]}')
                sys.exit()

        # check if last interval's end is the same as the timestep
        # if intervals is not empty
        if len(intervals) != 0:
            # assert intervals[-1][1] == timestep, f'Last interval end time {intervals[-1][1]} is not the same as timestep {timestep}'
            if intervals[-1][1] != timestep:
                print(f'Last interval end time {intervals[-1][1]} is not the same as timestep {timestep}')
                # sys.exit()
                print(f'Latency violation detected at timestep {timestep}')
        
        # check if energy status is over the energy capacity
        assert energy_stat <= self.energy_storage.energy_capacity, f'Energy status {energy_stat} is over the energy capacity {self.energy_storage.energy_capacity}'
        assert energy_stat >= 0, f'Energy status {energy_stat} is below 0'

        # get SOC (state of charge)
        if self.energy_storage.energy_capacity != 0:
            SOC = energy_stat / self.energy_storage.energy_capacity
        else:
            SOC = 0

        # Check if energy status > 0
        # if energy_stat <= 0:
        #     print('Energy depleted in node: ', self.node.name)
        #     sys.exit()
        return energy_stat, SOC
    
    def TXRX_match(self, dst_node_pmu):
        TRX_match = False
        for t in self.tx:
            for r in dst_node_pmu.rx:
                if t.check_TRX_match(r):
                    TRX_match = True
                    tx, rx = t, r
                    break
        if not TRX_match:
            print('Exit: Power transceiver not matched')
            sys.exit()
        return tx, rx
    
    def discharge_power(self, dynamic_power, start_time, duration, component, initial=False):
        if duration == 0:
            return
        # cycle lifetime - Assume linear capacity drop following the cycle number (this is modeled in the charge_power function)

        end_time = start_time + duration
        # round start_time and end_time to 6 decimal places
        start_time = round(float(start_time), 6)
        end_time = round(float(end_time), 6)

        if initial == True:
            self.power_consumption[(start_time, end_time)] = self.node.static_power + dynamic_power
            self.power_charge[(start_time, end_time)] = 0

            self.power_breakdown[(start_time, end_time)] = self.node.static_power_breakdown
            if component != None:
                self.power_breakdown[(start_time, end_time)][component] += dynamic_power
        else:
            additional_dynamic_power = dynamic_power
            
            intervals = list(self.power_consumption.keys())
            for log_start_time, log_end_time in intervals:
                # round log_start_time and log_end_time to 6 decimal places
                log_start_time = round(float(log_start_time), 6)
                log_end_time = round(float(log_end_time), 6)
                if util.check_overlap(start_time, end_time, log_start_time, log_end_time):
                    # Find overlapping interval
                    overlapping_interval = (max(log_start_time, start_time), min(log_end_time, end_time))
                    # round overlapping_interval to 6 decimal places
                    overlapping_interval = (round(float(overlapping_interval[0]), 6), round(float(overlapping_interval[1]), 6))

                    initial_power_consumption = self.power_consumption[(log_start_time, log_end_time)]
                    initial_power_charge = self.power_charge[(log_start_time, log_end_time)]
                    initial_power_breakdown = self.power_breakdown[(log_start_time, log_end_time)]
                    
                    # # Print all times
                    # print(f'Node: {self.node.name}, Operation: discharge power')
                    # print(f'Overlapping interval: {overlapping_interval}, Original interval: {(start, end)}')
                    
                    # Add power consumption stats
                    if log_start_time < overlapping_interval[0]:
                        self.power_consumption[(log_start_time, overlapping_interval[0])] = initial_power_consumption
                        self.power_charge[(log_start_time, overlapping_interval[0])] = initial_power_charge
                        self.power_breakdown[(log_start_time, overlapping_interval[0])] = copy.deepcopy(initial_power_breakdown)
                    if log_end_time > overlapping_interval[1]:
                        self.power_consumption[(overlapping_interval[1], log_end_time)] = initial_power_consumption
                        self.power_charge[(overlapping_interval[1], log_end_time)] = initial_power_charge
                        self.power_breakdown[(overlapping_interval[1], log_end_time)] = copy.deepcopy(initial_power_breakdown)
                    #if overlapping_interval[0] != overlapping_interval[1]:
                    self.power_consumption[overlapping_interval] = initial_power_consumption + additional_dynamic_power
                    self.power_charge[overlapping_interval] = initial_power_charge
                    self.power_breakdown[overlapping_interval] = copy.deepcopy(initial_power_breakdown)
                    self.power_breakdown[overlapping_interval][component] += additional_dynamic_power

                    # print("Discharge power, power consumption: ", self.power_consumption)

                    # Check if power consumption exceeds the power constraint
                    if self.node.power_constraint != 0 and self.power_consumption[overlapping_interval] > self.node.power_constraint:
                        REPORT_VIOLATION(self.node, overlapping_interval[0], overlapping_interval[1], 'power_constraint', self.power_consumption[overlapping_interval], self.node.power_constraint)
                    
                    # Delete original interval
                    if overlapping_interval != (log_start_time, log_end_time) and (log_start_time, overlapping_interval[0]) != (log_start_time, log_end_time) and (overlapping_interval[1], log_end_time) != (log_start_time, log_end_time):
                        del self.power_consumption[(log_start_time, log_end_time)]
                        del self.power_charge[(log_start_time, log_end_time)]
                        del self.power_breakdown[(log_start_time, log_end_time)]
            
            # Add voltage checking here
            # Assume linear voltage drop (e.g. 4.1V to 3.0V) following the SOC (e.g. 100% to 60%?)
            # Should add voltage parameters in user-defined yaml file
            if self.energy_storage.type in {'battery', 'supercapacitor'}: # check if energy storage type is battery or supercapacitor
                _ , SOC = self.get_energy_status(end_time)
                energy_storage_voltage = SOC_to_voltage(SOC=SOC, V_max=self.energy_storage.v_max, type=self.energy_storage.type)
                # check if energy_storage_voltage is sufficient to power the components of the node
                if self.node.check_voltage(energy_storage_voltage) == False:
                    print('Voltage not sufficient in node: ', self.node.name)
                    print(f'Energy storage voltage: {energy_storage_voltage}, Required voltage: {self.node.required_voltage}')
                    REPORT_VIOLATION(self.node, start_time, end_time, 'voltage', energy_storage_voltage, self.node.required_voltage)
                    # sys.exit()
        return

    def charge_power(self, rx, power, start_time, duration):
        if duration == 0:
            return
        end_time = start_time + duration
        # round start_time and end_time to 6 decimal places
        start_time = round(float(start_time), 6)
        end_time = round(float(end_time), 6)

        # Get the power transfer method
        method = rx.method if hasattr(rx, 'method') else 'RF'
        method_key = str(method).lower()
        
        # Calculate overlapping SAR from all concurrent power transmissions
        total_SAR, check_SAR = self.get_overlapping_SAR(start_time, end_time, power, method_key)
        
        # Check for SAR violation
        if total_SAR > self.node.required_SAR and check_SAR:
            REPORT_VIOLATION(self.node, start_time, end_time, 'SAR', total_SAR, self.node.required_SAR)
            print(f'SAR violation at node {self.node.name}: {total_SAR:.6f} W/kg > {self.node.required_SAR} W/kg')
        
        # restrict power based on SAR (individual transmission)
        restricted_power = self.restrict_power_based_on_SAR(power, self.node.required_SAR, method_key)

        recovered_power = rx.rectification_efficiency * restricted_power
        # print(f"Power received: {power}, Restricted power: {restricted_power}, Recovered power: {recovered_power}")
        self.charge_loss = (1 - rx.rectification_efficiency) * restricted_power * (1 - self.energy_storage.round_trip_efficiency)

        self.charge_time = self.energy_storage.initial_energy_capacity / (recovered_power * self.energy_storage.round_trip_efficiency)
        # print("Charge loss: ", self.charge_loss)

        # Coulombic efficiency (CE) of 99.96% is required for cycling stability up to 500 cycles for commercialization.
        # Reference: https://www.nature.com/articles/s41467-018-07599-8

        # increase the cycle count whenever the power is charged.
        
        intervals = list(self.power_charge.keys())
        for log_start_time, log_end_time in intervals:
            if util.check_overlap(start_time, end_time, log_start_time, log_end_time):
                # round log_start_time and log_end_time to 6 decimal places
                log_start_time = round(float(log_start_time), 6)
                log_end_time = round(float(log_end_time), 6)
                # Find overlapping interval
                overlapping_interval = (max(log_start_time, start_time), min(log_end_time, end_time))
                # round overlapping_interval to 6 decimal places
                overlapping_interval = (round(float(overlapping_interval[0]), 6), round(float(overlapping_interval[1]), 6))
                
                initial_power_consumption = self.power_consumption[(log_start_time, log_end_time)]
                initial_power_charge = self.power_charge[(log_start_time, log_end_time)]
                initial_power_breakdown = self.power_breakdown[(log_start_time, log_end_time)]
                
                # # Print all times
                # print(f'Node: {self.node.name}, Operation: discharge power')
                # print(f'Overlapping interval: {overlapping_interval}, Original interval: {(start, end)}')
                
                # Add power consumption stats
                if log_start_time < overlapping_interval[0]:
                    self.power_consumption[(log_start_time, overlapping_interval[0])] = initial_power_consumption
                    self.power_charge[(log_start_time, overlapping_interval[0])] = initial_power_charge
                    self.power_breakdown[(log_start_time, overlapping_interval[0])] = copy.deepcopy(initial_power_breakdown)
                if log_end_time > overlapping_interval[1]:
                    self.power_consumption[(overlapping_interval[1], log_end_time)] = initial_power_consumption
                    self.power_charge[(overlapping_interval[1], log_end_time)] = initial_power_charge
                    self.power_breakdown[(overlapping_interval[1], log_end_time)] = copy.deepcopy(initial_power_breakdown)
                #if overlapping_interval[0] != overlapping_interval[1]:
                self.power_consumption[overlapping_interval] = initial_power_consumption + self.charge_loss
                self.power_charge[overlapping_interval] = initial_power_charge + recovered_power * self.energy_storage.round_trip_efficiency
                self.power_breakdown[overlapping_interval] = copy.deepcopy(initial_power_breakdown)
                self.power_breakdown[overlapping_interval]['charge_loss'] += self.charge_loss

                # Check if power charge exceeds the power constraint
                if self.node.power_constraint != 0 and self.power_consumption[overlapping_interval] > self.node.power_constraint:
                    REPORT_VIOLATION(self.node, overlapping_interval[0], overlapping_interval[1], 'power_constraint', self.power_consumption[overlapping_interval], self.node.power_constraint)
                
                # Delete original interval
                if overlapping_interval != (log_start_time, log_end_time) and (log_start_time, overlapping_interval[0]) != (log_start_time, log_end_time) and (overlapping_interval[1], log_end_time) != (log_start_time, log_end_time):
                    del self.power_consumption[(log_start_time, log_end_time)]
                    del self.power_charge[(log_start_time, log_end_time)]
                    del self.power_breakdown[(log_start_time, log_end_time)]

                # decrease the capacity of the energy storage based on the coulombic efficiency
                self.energy_storage.energy_capacity = self.energy_storage.energy_capacity * self.energy_storage.coulombic_efficiency
        return
    
    def transmit_power(self, power, dst_node, start_time, duration):
        self.dynamic_power = 0
        tx, rx = self.TXRX_match(dst_node.pmu)
        tx.transmit_power(power)
        self.dynamic_power += tx.dynamic_power
        self.discharge_power(self.dynamic_power, start_time, duration, 'pmu')
        UPDATE_STATS(self.node, start_time, duration, 'transmit power', power_trx=tx)

        power_blocks = self.node.power_blocks
        power_blocks.append([power])
        return power_blocks
    
    def receive_power(self, power, src_node, prop_latency, start_time, duration):
        tx, rx = src_node.pmu.TXRX_match(self)
        rx.receive_power(power)
        self.charge_power(rx, power.signal_power, start_time, duration)
        UPDATE_STATS(self.node, start_time, duration, 'receive power', power_trx=rx)

        power_blocks = self.node.power_blocks
        power_blocks.append([power])
        return power_blocks
        
