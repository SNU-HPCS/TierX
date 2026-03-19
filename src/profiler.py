# Module for profiling
import os
import sys
import math
import matplotlib.pyplot as plt
sys.path.append('lib')
import Node.util as util

print_log = True
debugging = False
# Turn off for fast simulation - can be overridden by environment variable
plot_graph = os.getenv('TierX_PLOT_GRAPH', 'false').lower() == 'true'


def check_energy_status(node, start_time, end_time):
    initial_energy_ref, start_SOC_ref = node.pmu.get_energy_status(start_time)
    end_energy_ref, end_SOC_ref = node.pmu.get_energy_status(end_time)
    power_consumption = node.pmu.power_consumption[(start_time, end_time)]
    power_charge = node.pmu.power_charge[(start_time, end_time)]
    # Check if the energy status is consistent with the power consumption and charge
    end_energy = initial_energy_ref
    end_energy -= power_consumption * (end_time - start_time)
    end_energy += power_charge * (end_time - start_time)
    # Maximum energy status is bounded by the energy capacity
    end_energy = min(end_energy, node.pmu.energy_storage.energy_capacity)
    end_energy = max(end_energy, 0)
    assert(math.isclose(end_energy, end_energy_ref)), f"Energy status mismatch for node {node.name} at time {start_time} to {end_time}. Initial energy ref: {initial_energy_ref} End energy: {end_energy}, End energy ref: {end_energy_ref}, Power consumption: {power_consumption}, Power charge: {power_charge}, end_SOC_ref: {end_SOC_ref}, storage capacity: {node.pmu.energy_storage.energy_capacity}, power_consumption: {node.pmu.power_consumption}, power_charge: {node.pmu.power_charge}"

def ADD_STATS(node, start_time, latency, operation, data_trx=None, power_trx=None):
    if latency == 0:
        return
    
    end_time = start_time + latency
    # round start_time and end_time to 6 decimal places
    start_time = round(float(start_time), 6)
    end_time = round(float(end_time), 6)
    power_consumption = node.pmu.power_consumption[(start_time, end_time)]
    power_charge = node.pmu.power_charge[(start_time, end_time)]
    power_breakdown = node.pmu.power_breakdown[(start_time, end_time)]
    end_energy, SOC = node.pmu.get_energy_status(end_time)
    type = node.pmu.energy_storage.type
    if print_log and latency != 0:
        if type in {'battery', 'supercapacitor'}: # check if energy storage type is battery or supercapacitor
            print(f'Node: {node.name:>15}, Time(start,end): ({start_time:>10}, {end_time:>10}), Operation: {operation:>50}, Discharge(mW): {power_consumption:>10}, Charge(mW): {power_charge:.4f}, Energy status(mJ): {end_energy:.4f} ({100 * SOC:.2f} %)')
        else: # wired
            print(f'Node: {node.name:>15}, Time(start,end): ({start_time:>10}, {end_time:>10}), Operation: {operation:>50}, Discharge(mW): {power_consumption:>10}')
    
    data_trx_list = [data_trx] if data_trx else None
    power_trx_list = [power_trx] if power_trx else None
    
    node.schedule.append((start_time, end_time, operation, power_consumption, power_charge, end_energy, data_trx_list, power_trx_list, power_breakdown, SOC))

    # Sort log_stats by start_time
    node.schedule = sorted(node.schedule, key=lambda x: (x[0], x[1]))
    return

def UPDATE_STATS(node, start_time, duration, operation, data_trx=None, power_trx=None):
    if duration == 0:
        return
    end_time = start_time + duration
    new_schedule = []
    # round start_time and end_time to 6 decimal places
    start_time = round(float(start_time), 6)
    end_time = round(float(end_time), 6)

    # check if node.schedule is empty
    if not node.schedule:
        if print_log:
            print(f'Node: {node.name}, Operation: {operation}, Start: {start_time}, End: {end_time} - No logs to update')

    # Modify logs that overlap with the new log
    for i, node_log in enumerate(node.schedule):
        log_start_time, log_end_time, log_operation, log_power_consumption, log_power_charge, log_end_energy, log_data_trx_list, log_power_trx_list, log_power_breakdown, _ = node_log
        log_start_time = round(float(log_start_time), 6)
        log_end_time = round(float(log_end_time), 6)

        if util.check_overlap(start_time, end_time, log_start_time, log_end_time):
            chunk_start = max(start_time, log_start_time)
            chunk_end = min(end_time, log_end_time)
            # round chunk_start and chunk_end to 6 decimal places
            chunk_start = round(float(chunk_start), 6)
            chunk_end = round(float(chunk_end), 6)

            # Modify the log
            # 1. Split the front part
            if chunk_start > log_start_time:
                power_consumption = node.pmu.power_consumption[(log_start_time, chunk_start)]
                power_charge = node.pmu.power_charge[(log_start_time, chunk_start)]
                power_breakdown = node.pmu.power_breakdown[(log_start_time, chunk_start)]
                end_energy, SOC = node.pmu.get_energy_status(chunk_start)
                check_energy_status(node, log_start_time, chunk_start)
                # assert(math.isclose(end_energy + power_consumption * (chunk_start - log_start_time) - power_charge * (chunk_start - log_start_time), node.pmu.get_energy_status(log_start_time)[0])), f"Split front: Energy status mismatch for node {node.name} at time {log_start_time} to {chunk_start}"
                new_schedule.append((log_start_time, chunk_start, log_operation, power_consumption, power_charge, end_energy, log_data_trx_list, log_power_trx_list, power_breakdown, SOC))

            # 2. Split the end part
            if chunk_end < log_end_time:
                try:
                    power_consumption = node.pmu.power_consumption[(chunk_end, log_end_time)]
                except KeyError:
                    print(f"KeyError for (chunk_end, log_end_time): ({chunk_end}, {log_end_time})")
                    print(f"Chunk start: {chunk_start}, Chunk end: {chunk_end}")
                    print(f"Log start: {log_start_time}, Log end: {log_end_time}")
                    print(f"Node: {node.name}, Operation: {operation}")
                    print("Available keys in node.pmu.power_consumption:")
                    print(list(node.pmu.power_consumption.keys()))
                    raise
                power_charge = node.pmu.power_charge[(chunk_end, log_end_time)]
                power_breakdown = node.pmu.power_breakdown[(chunk_end, log_end_time)]
                end_energy, SOC = node.pmu.get_energy_status(log_end_time)
                check_energy_status(node, chunk_end, log_end_time)
                # assert(math.isclose(end_energy + power_consumption * (log_end_time - chunk_end) - power_charge * (log_end_time - chunk_end), node.pmu.get_energy_status(chunk_end)[0])), f"Split end: Energy status mismatch for node {node.name} at time {chunk_end} to {log_end_time}"
                new_schedule.append((chunk_end, log_end_time, log_operation,power_consumption, power_charge, end_energy, log_data_trx_list, log_power_trx_list, power_breakdown, SOC))
            
            # 3. Modify the overlapping chunk
            # 1) Add operation
            if 'idle' in log_operation:
                log_operation = operation
            else:
                if operation not in log_operation:
                    log_operation += f' + {operation}'
            
            # 2) Add power consumption, charge, energy status
            power_consumption = node.pmu.power_consumption[(chunk_start, chunk_end)]
            power_charge = node.pmu.power_charge[(chunk_start, chunk_end)]
            power_breakdown = node.pmu.power_breakdown[(chunk_start, chunk_end)]
            end_energy, SOC = node.pmu.get_energy_status(chunk_end)
            type = node.pmu.energy_storage.type
            
            # 3) Add data_trx
            if data_trx:
                if log_data_trx_list:
                    log_data_trx_list.append(data_trx)
                else:
                    log_data_trx_list = [data_trx]
            # 4) Add power_trx
            if power_trx:
                if log_power_trx_list:
                    log_power_trx_list.append(power_trx)
                else:
                    log_power_trx_list = [power_trx]
            if print_log:
                if type in {'battery', 'supercapacitor'}: # check if energy storage type is battery or supercapacitor
                    print(f'Node: {node.name:>15}, Time(start,end): ({chunk_start:.4f}, {chunk_end:.4f}), Operation: {log_operation:>50}, Power(mW): {power_consumption:>10}, Charge(mW): {power_charge:.4f}, Energy status(mJ): {end_energy:.4f} ({100 * SOC:.2f} %)')
                else: # wired
                    print(f'Node: {node.name:>15}, Time(start,end): ({chunk_start:.4f}, {chunk_end:.4f}), Operation: {log_operation:>50}, Power(mW): {power_consumption:>10}')

            check_energy_status(node, chunk_start, chunk_end)
            # assert(math.isclose(end_energy + power_consumption * (chunk_end - chunk_start) - power_charge * (chunk_end - chunk_start), node.pmu.get_energy_status(chunk_start)[0])), f"Overlap: Energy status mismatch for node {node.name} at time chunk_start: {chunk_start} to chunk_end: {chunk_end}, power_consumption: {power_consumption}, power_charge: {power_charge}, chunk_start energy: {node.pmu.get_energy_status(chunk_start)[0]}, chunk_end energy: {end_energy}"
            new_schedule.append((chunk_start, chunk_end, log_operation, power_consumption, power_charge, end_energy, log_data_trx_list, log_power_trx_list, power_breakdown, SOC))

        else:
            new_schedule.append(node_log)
        # Sort log_stats by start_time (primary) and end_time (secondary)
        new_schedule = sorted(new_schedule, key=lambda x: (x[0], x[1]))

    # Update log_stats
    node.schedule = new_schedule
    return

def REPORT_VIOLATION(node, start_time, end_time, operation, power_consumption, power_constraint):
    """
    Report a violation of power constraint.
    """
    log = f'Node: {node.name:>15}, Time(start,end): ({start_time:.4f}, {end_time:.4f}), Violation: {operation:>20}, Power consumption(mW): {power_consumption:>10}, Power constraint(mW): {power_constraint:>10}'
    node.net.violation_log.append(log)
    if print_log:
        print(log)
        # Write to log file
        # log_file.write(log + '\n')
    return

def PRINT_STATS(net, configname=None):
    latency_violation = False
    if print_log:
        print('====================')
        print('Stats summary')
        print('====================')
        for node in net.nodes:
            stats = node.schedule
            initial_energy_capacity = node.pmu.energy_storage.initial_energy_capacity
            print(f'Node: {node.name}, Energy Capacity(mJ): {initial_energy_capacity}')
            for stat in stats:
                if node.pmu.energy_storage.type in {'battery', 'supercapacitor'}: # check if energy storage type is battery or supercapacitor
                    print(f'Time(start,end): ({stat[0]:.4f}, {stat[1]:.4f}), Operation: {stat[2]:>50}, Discharge(mW): {stat[3]:>10}, Charge(mW): {stat[4]:.4f}, Energy status(mJ): {stat[5]:.4f} ({100 * stat[9]:.2f} %)')
                else: # wired
                    print(f'Time(start,end): ({stat[0]:.4f}, {stat[1]:.4f}), Operation: {stat[2]:>50}, Discharge(mW): {stat[3]:>10}, Charge(mW): {stat[4]:.4f}')
            print()
        
    
        # Print violations
        print('====================')
        print('Power constraint violations')
        print('====================')

        for log in net.violation_log:
            print(log)
    
    # Visualize the power consumption of each node
    # Each subplot shows the power consumption of the node
    # Each node has a different color
    if plot_graph:
        fig, ax = plt.subplots(len(net.nodes)+2, 1, figsize=(10, 20))
        if len(net.nodes) == 1:
            ax = [ax]

        # Dictionary to store unique legend handles
        handles_dict = {}

        for i, node in enumerate(net.nodes):
            stats = node.schedule
            start_times = [stat[0] for stat in stats]
            end_times = [stat[1] for stat in stats]
            power_consumptions = [stat[3] for stat in stats]
            power_charges = [stat[4] for stat in stats]
            end_energies = [stat[5] for stat in stats]
            power_breakdowns = [stat[8] for stat in stats]
            stats_list = list(zip(start_times, end_times, end_energies, power_consumptions, power_charges, power_breakdowns))
            # sort stats_list by start time and end time
            stats_list.sort(key=lambda x: (x[0], x[1]))
            
            for start, end, end_energy, total_power, power_charge, breakdown in stats_list:
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


                # Plot without labels to prevent duplicate legends
                h1 = ax[i].fill_between([start, end], 0, sensor_power, color='b', alpha=0.3, edgecolor="none")
                h2 = ax[i].fill_between([start, end], sensor_power, sensor_power + processor_power, color='r', alpha=0.3, edgecolor="none")
                h3 = ax[i].fill_between([start, end], sensor_power + processor_power, sensor_power + processor_power + comm_power, color='g', alpha=0.3, edgecolor="none")
                h4 = ax[i].fill_between([start, end], sensor_power + processor_power + comm_power, sensor_power + processor_power + comm_power + pmu_power, color='y', alpha=0.3, edgecolor="none")
                h5 = ax[i].fill_between([start, end], sensor_power + processor_power + comm_power + pmu_power, sensor_power + processor_power + comm_power + pmu_power + charge_loss, color='c', alpha=0.3, edgecolor="none")

                # Store handles only once (first encounter)
                if 'sensor' not in handles_dict:
                    handles_dict['sensor'] = h1
                if 'processor' not in handles_dict:
                    handles_dict['processor'] = h2
                if 'comm_link' not in handles_dict:
                    handles_dict['comm_link'] = h3
                if 'pmu' not in handles_dict:
                    handles_dict['pmu'] = h4
                if 'charge_loss' not in handles_dict:
                    handles_dict['charge_loss'] = h5
                
                # Plot SOC in ax[3]
                # SOC = end_energy / node.pmu.energy_storage.initial_energy_capacity * 100
                SOC = node.pmu.get_energy_status(end)[1] * 100  # Get SOC directly
                # Different color for each node
                color = 'red' if i == 0 else 'orange' if i == 1 else 'yellow' if i == 2 else 'm'
                if start == 0:
                    SOC_before = node.pmu.energy_storage.initial_charge
                ax[3].plot([start, end], [SOC_before, SOC], marker='o', color=color, label=f'Node {node.name} SOC', linewidth=2)
                
                # Collect SOC values for setting y-axis limits later
                if not hasattr(ax[3], 'soc_values'):
                    ax[3].soc_values = []
                ax[3].soc_values.extend([SOC_before, SOC])
                SOC_before = SOC  # Update SOC_before for the next iteration
                
                # Plot power charge in ax[4]
                ax[4].plot([start, end], [power_charge, power_charge], color=color, label=f'Node {node.name} Power Charge', linewidth=2)
                ax[4].set_ylabel('Power Charge (mW)')
                ax[4].set_title('Power Charge of Nodes')
                ax[4].set_xlim(0, net.required_latency + net.trial_period * net.num_trials)
            
            ax[3].set_ylabel('SOC (%)')
            ax[3].set_title('State of Charge (SOC) of Nodes')
            ax[3].set_xlim(0, net.required_latency + net.trial_period * net.num_trials)

            # Add power constraint line
            if node.power_constraint != 0:
                ax[i].axhline(y=node.power_constraint, color='r', linestyle='dashed', label='Power Constraint')
            
            # Add required latency line
            required_latency = net.required_latency + net.trial_period * net.num_trials
            ax[i].axvline(x=required_latency, color='g', linestyle='dashed', label='Required Latency')

            ax[i].set_title(f"Node {node.name} Power Consumption")
            ax[i].set_ylabel(f"Node {node.name} Power (mW)")
            ax[i].grid(True)

        # Set legend in first subplot
        ax[0].legend(handles_dict.values(), handles_dict.keys(), loc='upper right')

        ax[-1].set_xlabel("Time (ms)")
    
    # Check if any log after the required latency is not idle
    required_latency = net.required_latency + net.trial_period * net.num_trials
    latency_violation = False
    for node in net.nodes:
        for log in node.schedule:
            if log[1] >= required_latency and ('compute' in log[2] or 'transmit data' in log[2] or 'receive data' in log[2]):
                print(f'Log for node {node.name} after required latency is not idle: {log[2]} (start: {log[0]}, end: {log[1]})')
                # add log to the plot
                if plot_graph:
                    plt.suptitle(f'Log for node {node.name} after required latency is not idle: {log[2]} (start: {log[0]}, end: {log[1]})')
                latency_violation = True
                break
        if latency_violation:
            break

    if plot_graph:
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])

    # Save plot to file
    if debugging:
        plt.savefig('power_consumption.png')
    else:
        if plot_graph:
            plt.savefig(f'plots/{configname}.png')
    plt.close()
    
    # Check for average power violation for each node
    avg_power_violation = False
    avg_power_violation_log = []
    simulation_duration = required_latency  # Total simulation time
    
    for node in net.nodes:
        # Skip nodes without average power constraint (constraint == 0)
        if node.avg_power_constraint == 0:
            continue
            
        # Calculate total energy consumed by this node
        total_energy = 0  # in mJ (power in mW * time in ms)
        for log in node.schedule:
            start_time, end_time = log[0], log[1]
            power_consumption = log[3]  # in mW
            duration = end_time - start_time  # in ms
            total_energy += power_consumption * duration
        
        # Calculate average power (mJ / ms = mW)
        if simulation_duration > 0:
            avg_power = total_energy / simulation_duration
        else:
            avg_power = 0
        
        # Check if average power exceeds the constraint
        if avg_power > node.avg_power_constraint:
            avg_power_violation = True
            violation_msg = f'Node: {node.name:>15}, Average Power Violation: {avg_power:.4f} mW > {node.avg_power_constraint} mW (constraint)'
            avg_power_violation_log.append(violation_msg)
            if print_log:
                print(violation_msg)
    
    if print_log and avg_power_violation_log:
        print('====================')
        print('Average power constraint violations')
        print('====================')
        for log in avg_power_violation_log:
            print(log)
    
    # peak_power_violation: 1 if any violation in violation_log, 0 otherwise
    peak_power_violation = 1 if net.violation_log else 0
    
    return peak_power_violation, avg_power_violation, latency_violation
