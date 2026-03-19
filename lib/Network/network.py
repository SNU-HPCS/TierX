import yaml
import numpy as np
from scipy.special import erfcinv
import sys, math
sys.path.append('..')
import profiler
import Node.util as util


class Network():
    def __init__(self, nodes, app, hw, channel, env):
        self.nodes = nodes
        self.app = app
        self.hw = hw
        self.channel = channel
        self.env = env
        self.violation_log = []

        self.num_nodes = len(nodes)
        for node in nodes:
            node.net = self  # Set the network reference for each node
        self.required_latency = self.app['required_latency']
        self.required_BER = float(self.app['required_BER'])
        self.trial_duration = self.app['trial_duration']
        self.trial_period = self.app['trial_period']
        self.num_trials = self.app['num_trials']
        if self.trial_period < self.trial_duration:
            print('Exit: Trial period cannot be less than trial duration')
            sys.exit()
        self.total_power = 0

    class PowerBlock():
        def __init__(self):
            self.type = 'power'
            self.wireless = False
            self.method = ''
            self.signal_power = 0
            self.frequency = 0
            self.bandwidth = 0
    
    def node_byname(self, node_name):
        # Return idx of node
        for node in self.nodes:
            if node_name == node.name:
                return node
        
        print('Node not found')
        assert(0)
    
    def parse_comm_network(self, net):
        net_list = [[]]
        if net is not None:
            for conn in net:
                if 'barrier' in conn:
                    # Barrier for computational dependencies
                    # A barrier requires all prior dst nodes to finish their computation before proceeding
                    # A barrier separates different phases of computation by starting a new list
                    net_list.append([])
                else:
                    # Normal communication link
                    if 'src' not in conn or 'dst' not in conn:
                        print('Exit: src or dst not found in connection')
                        sys.exit()
                    src_id, dst_id = self.node_byname(conn['src']).id, self.node_byname(conn['dst']).id
                    net_list[-1].append((src_id, dst_id))
            # print(net_list)
        return net_list
    
    def parse_power_network(self, net):
        net_list = []
        if net is not None:
            for conn in net:
                src_id, dst_id = self.node_byname(conn['src']).id, self.node_byname(conn['dst']).id
                net_list.append((src_id, dst_id))
        return net_list
    
    def transfer_data(self, src, dst):
        # Check communication link between src and dst
        src_node, dst_node = self.nodes[src], self.nodes[dst]
        print(f'Transferring data from {src_node.name} to {dst_node.name}')
        # Transmit data
        src_node.transmit_data(dst_node)
        # Propagation
        prop_channel = self.channel[src][dst]
        latency = prop_channel.propagate(src_node, dst_node, 'data')
        # Receive data
        BER, retransmission_num = dst_node.receive_data(src_node, latency)
        
        # # Check BER
        # required_BER = float(self.app['required_BER'])
        # if BER > required_BER:
        #     print('Error: BER exceeds threshold')
        #     sys.exit()
        # else:
        #     print('BER:', BER)
        
        return BER, retransmission_num
    
    def transfer_power(self, src, dst, power, start_time, duration):
        # Transmit power from src to dst
        src_node, dst_node = self.nodes[src], self.nodes[dst]
        print(f'Transferring power from {src_node.name} to {dst_node.name} at {start_time:.4f} to {start_time + duration:.4f}')
        
        # Check for overlapping SAR violations before transmitting
        if hasattr(src_node, 'pmu') and src_node.pmu.tx:
            tx = src_node.pmu.tx[0]
            method = tx.method if hasattr(tx, 'method') else 'RF'
            method_key = str(method).lower()
            tx_power_mw = tx.radiated_power if hasattr(tx, 'radiated_power') else 0
            total_SAR, check_SAR = dst_node.pmu.get_overlapping_SAR(start_time, start_time + duration, tx_power_mw, method_key)
            
            if total_SAR > dst_node.required_SAR and check_SAR:
                print(f'Warning: Overlapping SAR violation detected when transferring power to {dst_node.name}')
                print(f'Total SAR: {total_SAR:.6f} W/kg, Required: {dst_node.required_SAR} W/kg')
        
        # Transmit power
        src_node.transmit_power(power, dst_node, start_time, duration)
        # Propagation
        prop_channel = self.channel[src][dst]
        latency = prop_channel.propagate(src_node, dst_node, power)
        # Receive power
        dst_node.receive_power(power, src_node, latency, start_time, duration)
    
    def check_interference(self, tx, rx, chunk_start, chunk_end):
        # Check if there is interference in the entire network
        for node in self.nodes:
            for node_log in node.schedule:
                start_time, end_time, operation, power_consumption, power_charge, end_energy, interfering_data_trx_list, interfering_power_trx_list, _, _ = node_log
                if util.check_overlap(start_time, end_time, chunk_start, chunk_end):
                    # Check if there is interference
                    if interfering_data_trx_list is not None:
                        return tx.check_TRX_interference(interfering_data_trx_list)
                    # if interfering_power_trx_list is not None:
                    #     return tx.check_TRX_interference(interfering_power_trx_list)
        return False

    def run(self):
        simulation_end = self.num_trials * self.trial_period
        # Network setup
        power_net = self.hw['power_schedule']
        comm_net = self.hw['comm_schedule']
        # print("comm_net: ", comm_net)

        comm_net_list = self.parse_comm_network(comm_net)
        power_net_list = self.parse_power_network(power_net)

        # Each phase consists of processing and communication
        # processing: sense + compute
        # communication: transmit data for the next phase
        # print("comm_net_list: ", comm_net_list)
        num_phase_barriers = len(comm_net_list)

        # Latency breakdown
        is_latency_breakdown = False

        # Sense periodically
        # Add sensing intervals to the schedule
        for src, dst in comm_net_list[0]:
            node = self.nodes[src]
            if node.type == 'implant':
                node.sense(simulation_end, self.trial_duration, self.trial_period, self.required_latency)
                if is_latency_breakdown:
                    node.pmu.discharge_power(0, simulation_end + self.required_latency, 11000 - self.required_latency - simulation_end, None, initial=True)
                    profiler.ADD_STATS(node, simulation_end + self.required_latency, 11000 - self.required_latency - simulation_end, 'idle')

        for node in self.nodes:
            if node.type != 'implant':
                if is_latency_breakdown:
                    node.pmu.discharge_power(0, 0, 11000, None, initial=True)
                    profiler.ADD_STATS(node, 0, 11000, 'idle')
                else:
                    node.pmu.discharge_power(0, 0, simulation_end + self.required_latency, None, initial=True)
                    profiler.ADD_STATS(node, 0, simulation_end + self.required_latency, 'idle')

        
        # Computation
        for barrier in range(num_phase_barriers):
            # Compute for all src nodes
            for src, dst in comm_net_list[barrier]:
                print('Nodes: ', self.nodes[src].name, self.nodes[dst].name)
                node = self.nodes[src]
                # Perform computation
                if node.data_blocks:
                    node.compute(self.num_trials)
            # Computation done for all src nodes
        
            # Communication from source to destination nodes
            for src, dst in comm_net_list[barrier]:
                # Transfer data from src to dst, only if src,dst nodes are different and src has data blocks
                if src != dst and node.data_blocks:
                    # print("src:", src, "dst:", dst)
                    # print('Transferring data from', self.nodes[src].name, 'to', self.nodes[dst].name)
                    if src == 0:
                        BER, retransmission_num = self.transfer_data(src, dst)
                    else:
                        self.transfer_data(src, dst)
            
            print('====================')
            print('End of phase', barrier)
            print('====================')
        
        # Compute last phase
        for src, dst in comm_net_list[-1]:
            node = self.nodes[dst]
            if src != dst and node.data_blocks:
                last_data_blocks = node.compute(self.num_trials)

        print()
        print('====================')
        print('End of last phase')
        print('Time: ', simulation_end, 'ms')
        print('====================')
        print()

        # profiler.PRINT_STATS(self)
        
        # Add power management
        if self.env['realtime_charging'] == True:
            print("Adding power management")
            # Add power management for all nodes
            # Reverse order of power_net_list
            power_net_list = power_net_list[::-1]
            print('Power network list:', power_net_list)
            # print(power_net_list)
            for src, dst in power_net_list:
                # Transmit power from src to dst
                src_node, dst_node = self.nodes[src], self.nodes[dst]
                print(f'Checking power transfer from {src_node.name} to {dst_node.name}')
                tx, rx = src_node.pmu.TXRX_match(dst_node.pmu)
                
                charge_times = []
                # Implementation: check operation logs
                for node_log in dst_node.schedule:
                    start_time, end_time, operation, power_consumption, power_charge, end_energy, _, _, _, _ = node_log
                
                    # 1) Check energy status of RX
                    # Energy status is determined by the power consumption + leakage
                    # At a certain timestep, energy status can be calculated by interpolation
                    # start_energy = end_energy + power_consumption * (end_time - start_time) - power_charge * (end_time - start_time)
                    start_energy, _ = dst_node.pmu.get_energy_status(start_time)
                    # Check if energy status is correct
                    
                    energy_capacity = dst_node.pmu.energy_storage.energy_capacity
                    if end_energy < self.env['energy_min'] / 100 * energy_capacity:
                        charge_end = end_time
                        # Find timestep where energy status is at threshold
                        # Interpolate to find exact time
                        if start_energy < self.env['energy_min'] / 100 * energy_capacity:
                            charge_start = start_time
                        else:
                            charge_start = start_time - (start_energy - self.env['energy_min'] / 100 * energy_capacity) / power_consumption
                            if charge_start < start_time or charge_start >= end_time:
                                charge_start = -1
                                sys.exit('Exit: charge_start not found')

                        # 2) Check power consumption of TX between charge_start and charge_end
                        for src_node_log in src_node.schedule:
                            src_start_time, src_end_time, src_operation, src_power_consumption, src_power_charge, src_end_energy, _, _, _, _ = src_node_log
                            if util.check_overlap(src_start_time, src_end_time, charge_start, charge_end):
                                chunk_start = max(src_start_time, charge_start)
                                chunk_end = min(src_end_time, charge_end)
                                # Check power consumption
                                if src_power_consumption + tx.dynamic_power < src_node.power_constraint or src_node.power_constraint == 0:
                                    # 3) Check interference issues
                                    interference = self.check_interference(tx, rx, chunk_start, chunk_end)
                                    if not interference:
                                        charge_times.append((chunk_start, chunk_end))
                                    else:
                                        print(f'Interference detected at chunk {chunk_start:.4f} to {chunk_end:.4f}')
                                else:
                                    print('Power consumption exceeds constraint at node', src_node.name)
                    else:
                        print('Energy status is sufficient at node', dst_node.name)
                
                # Charging times are determined by above procedure
                # print('Charging times', charge_times)
                for charge_start, charge_end in charge_times:
                    charge_duration = charge_end - charge_start
                    power = self.PowerBlock()
                    self.transfer_power(src, dst, power, charge_start, charge_duration)
                
                # profiler.PRINT_STATS(self)
        
        self.BER = BER
        self.retransmission_num = retransmission_num

        # Calculate average power consumption of three nodes in total
        total_consumed_energy = 0
        total_implant_energy = 0
        total_near_energy = 0
        total_off_energy = 0
        # if not self.violation_log:
        for node in self.nodes:
            for node_log in node.schedule:
                start_time, end_time, operation, power_consumption, power_charge, end_energy, interfering_data_trx_list, interfering_power_trx_list, _, _ = node_log
                total_consumed_energy += power_consumption * (end_time - start_time)
                if node.type == 'implant':
                    total_implant_energy += power_consumption * (end_time - start_time)
                elif node.type == 'onbody':
                    total_near_energy += power_consumption * (end_time - start_time)
                elif node.type == 'external':
                    total_off_energy += power_consumption * (end_time - start_time)
        # average_power_consumption = total_consumed_energy / (simulation_end + self.required_latency)
        average_power_consumption = (total_implant_energy*200 + total_near_energy*15 + total_off_energy*15) / (simulation_end + self.required_latency) / 230
        implant_power_consumption = total_implant_energy / (simulation_end + self.required_latency)


        # Calculate lifetime of the implant
        implant_node = self.nodes[0]
        energy_stat, SOC_ratio = implant_node.pmu.get_energy_status(simulation_end + self.required_latency)
        SOC_at_end = energy_stat / implant_node.pmu.energy_storage.energy_capacity * 100
        # SOC_at_end = SOC_ratio * 100
        print(f'SOC at end of simulation: {SOC_at_end}')
        # Initial charge: 100%
        # if math.isclose(SOC_at_end, 1.0, rel_tol=1e-9):
        #     print('Implant is fully charged at the end of simulation')
        #     lifetime = float('100000000')
        # else:
        #     lifetime = (simulation_end + self.required_latency) / (1 - SOC_at_end) / 1000 # in seconds
        # print(f'Implant lifetime: {lifetime:.2f} ms')

        # lifetime
        
        max_lifetime = implant_node.pmu.energy_storage.max_lifetime
        initial_SOC = implant_node.pmu.energy_storage.initial_charge
        min_charge_status = implant_node.pmu.energy_storage.min_charge_status

        if SOC_at_end >= initial_SOC:
            lifetime = max_lifetime
        else:
            # The lifetime is calculated based on the initial charge and the SOC at the end of the simulation
            lifetime = min(max_lifetime, (simulation_end + self.required_latency) * initial_SOC / (initial_SOC - SOC_at_end) / 1000 / 3600) # in hours
        print(f'Implant initial charge: {initial_SOC}')
        print(f'Implant SOC at end of simulation: {SOC_at_end}')
        print(f'Implant lifetime: {lifetime} hours')

        # else:
        #     average_power_consumption = 10000  # Set to a high value if there are violations

        # Calculate average power breakdown
        total_power_breakdown = {
            'on': {},
            'near': {},
            'off': {}
        }

        is_power_breakdown = False

        if is_power_breakdown:
            for i, node in enumerate(self.nodes):
                stats = node.schedule
                start_times = [stat[0] for stat in stats]
                end_times = [stat[1] for stat in stats]
                power_consumptions = [stat[3] for stat in stats]
                power_charges = [stat[4] for stat in stats]
                end_energies = [stat[5] for stat in stats]
                power_breakdowns = [stat[8] for stat in stats]
                
                # print(f"Node {node.id} Start times: {start_times}")
                # print(f"Node {node.id} End times: {end_times}")
                # print(f"Node {node.id} Power Consumption: {power_consumptions}")
                stats_list = list(zip(start_times, end_times, end_energies, power_consumptions, power_charges, power_breakdowns))
                # sort stats_list by start time and end time
                stats_list.sort(key=lambda x: (x[0], x[1]))

                sensor_energy = 0
                processor_energy = 0
                comm_energy = 0
                pmu_energy = 0
                charge_loss_energy = 0
                charged_energy = 0

                for start, end, end_energy, total_power, power_charge, breakdown in stats_list:
                    # if total_power != sum(breakdown.values()):
                    if not math.isclose(total_power, sum(breakdown.values()), rel_tol=1e-9):
                        print("Exit: Total power doesn't match for node", node.name)
                        print("Time: ", start, "to", end)
                        print("Total power: ", total_power, "breakdown sum: ", sum(breakdown.values()))
                        print(breakdown.items())
                        sys.exit()

                    sensor_power = breakdown['sensor'] if node.type == 'implant' else 0
                    processor_power = breakdown['processor']
                    comm_power = breakdown['comm_link']
                    pmu_power = breakdown['pmu']
                    charge_loss = breakdown['charge_loss'] 

                    sensor_energy += sensor_power * (end - start)
                    processor_energy += processor_power * (end - start)
                    comm_energy += comm_power * (end - start)
                    pmu_energy += pmu_power * (end - start)
                    charge_loss_energy += charge_loss * (end - start)
                    charged_energy += power_charge * (end - start)

                if i == 0:
                    # on implant node
                    total_power_breakdown['on'] = {
                        'sensor': sensor_energy / (simulation_end + self.required_latency),
                        'processor': processor_energy / (simulation_end + self.required_latency),
                        'comm_link': comm_energy / (simulation_end + self.required_latency),
                        'pmu': pmu_energy / (simulation_end + self.required_latency),
                        'charge_loss': charge_loss_energy / (simulation_end + self.required_latency),
                        'charged_power': charged_energy / (simulation_end + self.required_latency)
                    }
                elif i == 1:
                    # near implant node
                    total_power_breakdown['near'] = {
                        'sensor': sensor_energy / (simulation_end + self.required_latency),
                        'processor': processor_energy / (simulation_end + self.required_latency),
                        'comm_link': comm_energy / (simulation_end + self.required_latency),
                        'pmu': pmu_energy / (simulation_end + self.required_latency),
                        'charge_loss': charge_loss_energy / (simulation_end + self.required_latency),
                        'charged_power': charged_energy / (simulation_end + self.required_latency)
                    }
                else: # elif i == 2:
                    # off implant node
                    total_power_breakdown['off'] = {
                        'sensor': sensor_energy / (simulation_end + self.required_latency),
                        'processor': processor_energy / (simulation_end + self.required_latency),
                        'comm_link': comm_energy / (simulation_end + self.required_latency),
                        'pmu': pmu_energy / (simulation_end + self.required_latency),
                        'charge_loss': charge_loss_energy / (simulation_end + self.required_latency),
                        'charged_power': charged_energy / (simulation_end + self.required_latency)
                    }
            
            # Print power breakdown for each node
            for node_type, breakdown in total_power_breakdown.items():
                print(f"Power breakdown for {node_type} node:")
                for component, power in breakdown.items():
                    print(f"  {component}: {power:.4f} mW")
                print()

        # Calculate latency
        last_data_block = last_data_blocks[-1][-1]
        if last_data_block is None:
            print('Exit: Last data block is None')
            sys.exit()
        duration = last_data_block.start_time + last_data_block.duration

        # Save node schedule for latency breakdown
        total_node_schedule = []
        # for node in self.nodes:
        #     total_node_schedule.append(node.schedule)

        # Create latency breakdown data structure for Gantt chart analysis
        latency_breakdown = {
            'on': {'operations': [], 'latency_components': {}},
            'near': {'operations': [], 'latency_components': {}},
            'off': {'operations': [], 'latency_components': {}},
            'duration': duration,
            'input_duration': self.trial_duration,
            'compute_latency': 0,
            'communication_latency': 0
        }



        # print(f"node schedule 0: {self.nodes[0].schedule}")
        # print(f"node schedule 1: {self.nodes[1].schedule}")
        # print(f"node schedule 2: {self.nodes[2].schedule}")

        if is_latency_breakdown:

            all_sorted_node_schedule_list = []
            for i, node in enumerate(self.nodes):
                if i == 0:
                    node_type_key = 'on'
                    print("On-node schedule:")
                    print(node.schedule)
                elif i == 1:
                    node_type_key = 'near'
                    print("Near-node schedule:")
                    print(node.schedule)
                else:
                    node_type_key = 'off'
                    print("Off-node schedule:")
                    print(node.schedule)

                for node_log in node.schedule:
                    start_time, end_time, operation, power_consumption, power_charge, end_energy, _, _, _, _ = node_log
                    operation_duration = end_time - start_time

                    if start_time >= self.trial_duration and end_time <= last_data_block.start_time + last_data_block.duration:
                        all_sorted_node_schedule_list.append((start_time, end_time, operation))

                    # Skip operations with zero duration
                    if operation_duration <= 0:
                        continue

                    if end_time > self.trial_duration and start_time < last_data_block.start_time + last_data_block.duration:
                        # Initialize operation in latency_components if not exists
                        if operation not in latency_breakdown[node_type_key]['latency_components']:
                            latency_breakdown[node_type_key]['latency_components'][operation] = 0

                        latency_breakdown[node_type_key]['latency_components'][operation] += min(end_time,last_data_block.start_time + last_data_block.duration) - max(start_time, self.trial_duration)

                    # Parse multiple operations from the operation string
                    # Operations can be combined with '+' like 'compute + transmit power'
                    operation_parts = [part.strip() for part in operation.split('+')]
                    operation_categories = []
                    
                    for part in operation_parts:
                        part_lower = part.lower()
                        category = None
                        
                        # Categorize each operation part
                        if 'sense' in part_lower:
                            category = 'sensing'
                        elif 'compute' in part_lower:
                            category = 'computation' 
                        elif 'data' in part_lower:
                            if 'transmit' in part_lower:
                                category = 'data_transmission'
                            elif 'receive' in part_lower:
                                category = 'data_reception'
                            # Remove data_processing - not a valid category
                        elif 'power' in part_lower:
                            if 'transmit' in part_lower:
                                category = 'power_transmission'
                            elif 'receive' in part_lower:
                                category = 'power_reception'
                            # Remove power_processing - not a valid category
                        elif 'idle' in part_lower:
                            category = 'idle'
                        
                        # Only add non-idle and non-None categories
                        if category and category != 'idle':
                            operation_categories.append(category)
                    
                    # If no meaningful operations found, skip this log entry
                    if not operation_categories:
                        continue
                    
                    # Create operation data for each category found
                    for i, category in enumerate(operation_categories):
                        operation_data = {
                            'category': category,
                            'start_time': start_time,
                            'end_time': end_time,
                            'duration': operation_duration,
                            'operation_name': operation,
                            'power_consumption': power_consumption,
                            'power_charge': power_charge,
                            'y_offset': i  # For handling overlapping operations in Gantt chart
                        }
                        
                        latency_breakdown[node_type_key]['operations'].append(operation_data)
                    
                    # Note: total_latency is not calculated here as it's meaningless
                    # The actual latency should be used from the simulation result

            # loop through communication_schedule_list and compute_schedule_list
            
            communication_schedule_list = []
            compute_schedule_list = []
            
            # sort all_sorted_node_schedule_list by the start_time, and secondary end_time
            all_sorted_node_schedule_list.sort(key=lambda x: (x[0], x[1]))
            for node_sched in all_sorted_node_schedule_list:
                
                start_time, end_time, operation = node_sched
                
                # check for communication and compute operations
                if "transmit data" in operation or "receive data" in operation:
                    if len(communication_schedule_list) == 0:
                        communication_schedule_list.append((start_time, end_time))
                    else:
                        ref_start_time, ref_end_time = communication_schedule_list[-1]
                        if start_time >= ref_start_time and start_time < ref_end_time:
                            # Merge overlapping intervals
                            communication_schedule_list[-1] = (ref_start_time, max(end_time, ref_end_time))
                        else:
                            communication_schedule_list.append((start_time, end_time))

                if "compute" in operation:
                    if len(compute_schedule_list) == 0:
                        compute_schedule_list.append((start_time, end_time))
                    else:
                        ref_start_time, ref_end_time = compute_schedule_list[-1]
                        if start_time >= ref_start_time and start_time < ref_end_time:
                            # Merge overlapping intervals
                            compute_schedule_list[-1] = (ref_start_time, max(end_time, ref_end_time))
                        else:
                            compute_schedule_list.append((start_time, end_time))
                                
            for comm_sched in communication_schedule_list:
                latency_breakdown['communication_latency'] += comm_sched[1] - comm_sched[0]
            for comp_sched in compute_schedule_list:
                latency_breakdown['compute_latency'] += comp_sched[1] - comp_sched[0]
                

        latency = last_data_block.start_time + last_data_block.duration - self.trial_duration

        # print("latency breakdown", latency_breakdown)
        # print("total latency", latency)
        # print("compute latency", latency_breakdown['compute_latency'])
        # print("communication latency", latency_breakdown['communication_latency'])
        # print("compute schedule list", compute_schedule_list)
        # print("all_sorted_node_schedule_list", all_sorted_node_schedule_list)

        # sys.__stdout__.write(f'Average power consumption: {average_power_consumption} mW\n')
        # sys.__stdout__.write(f'Latency: {latency} ms\n')
        
        return BER, retransmission_num, lifetime, latency, implant_power_consumption, total_power_breakdown, latency_breakdown