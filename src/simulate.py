import yaml
import copy
import os
import numpy as np
import sys
sys.path.append('lib')
import Node.node as node
import Network.network as network
import PropagationChannel.propagationChannel as propagationChannel
import profiler


def load_tierx_config(path: str = None):
    """Load TierX.yaml config; fall back to empty dict if missing."""
    if path is None:
        path = os.environ.get('TIERX_CONFIG')
        if not path:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            path = os.path.join(base_dir, 'TierX.yaml')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


if __name__ == '__main__':
    # Read cfg file for simulation
    with open('src/run.yaml', 'r') as f:
        sim_config = yaml.full_load(f)

    # Load TierX config for power constraints, input scaling, pipelining, and SRAM model
    tierx_config = load_tierx_config()
    power_constraints = tierx_config.get('power_constraints', {})
    input_scaling = tierx_config.get('input_scaling', {})
    pipelining = tierx_config.get('pipelining', {})
    sram_model = tierx_config.get('sram_model', {'enabled': False})

    # Run one workload
    if len(sim_config['workloads']) > 1:
        print('Multiple workloads specified, but only one will be run.')
    workload_path = f'lib/Input/{sim_config["workloads"][0]}.yaml'
    with open(workload_path, 'r') as f:
        workload_config = yaml.full_load(f)
    
    application_qos = workload_config['application']
    hardware_requirement = workload_config['hardware_spec']
    environment = workload_config['environment']
    
    # Initialize the nodes
    num_nodes = hardware_requirement['num_nodes']
    node_spec_list = []
    nodes = []
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
            print(f'Node {node_name} created with ID {node_id}')
            nodes.append(node_tmp)
            node_spec_list.append(hardware_requirement['nodes'][d])
            node_id += 1

    # path loss differs according to location, distance, method, frequency, ...
    # exists for each pair of nodes
    # path loss is fully decided only when HW configuration is done
    prop_channels = {}
    for d1 in range(num_nodes):
        for d2 in range(num_nodes):
            if d1 != d2:
                if d1 in prop_channels.keys():
                    prop_channels[d1][d2] = propagationChannel.PropagationChannel(node_spec_list[d1], node_spec_list[d2], environment)
                else:
                    prop_channels[d1] = {d2: propagationChannel.PropagationChannel(node_spec_list[d1], node_spec_list[d2], environment)}

    # Configure the network
    net = network.Network(nodes, application_qos, hardware_requirement, prop_channels, environment)

    # Run simulation
    net.run()

    print('====================')
    print('Simulation finished')
    print('====================')
    
    profiler.debugging = True
    profiler.PRINT_STATS(net)
    
