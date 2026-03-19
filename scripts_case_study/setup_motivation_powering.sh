#!/bin/bash
# setup_motivation_powering.sh
# Apply motivation powering changes, then roll back to original
# Uses sed to modify only necessary values, preserving other settings like pipelining

set -e

# Source yaml update functions
source lib/yaml_update.sh

manual_set=False

if [ $manual_set = False ]; then
    echo -e "🚨 You should manually change src/generate_all_configs.py 🚨"
    echo -e "🚨 Reduce the energy_density of a supercapacitor to 1 🚨"
    echo "
            'SMALL-CAP': {
                'implant_0': {
                    'type': 'supercapacitor',
                    'weight': 0.0001,
                    'energy_density': 1,
                    'round_trip_efficiency': 0.95,
                    'self_discharge_rate': 0.1,
                    'initial_charge': cap_initial_charge,
                    'charge_time': charge_time,  # in hrs
                    'max_lifetime': max_lifetime,  # in hrs
                    'min_charge_status': min_charge_status,
                    'v_max': 4.2,
                    'coulombic_efficiency': 1
                },
    "
    echo "If done, set manual_set=True in setup_motivation_powering.sh."
    exit 1
fi

# Backup originals
cp lib/Node/node.py lib/Node/node.py.bak
cp src/DSE.py src/DSE.py.bak
cp src/Plot_graph.py src/Plot_graph.py.bak
cp TierX.yaml TierX.yaml.bak
cp run.sh run.sh.bak

echo "Applying motivation powering changes..."

# node.py: set power_constraint
sed -i "s/self.power_constraint = 15 # in mW = .*/self.power_constraint = 30 # in mW/" lib/Node/node.py
sed -i "s/self.power_constraint = 200 # in mW = .*/self.power_constraint = 50 # in mW/" lib/Node/node.py

# Update DSE.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_power'|" src/DSE.py
# Update Plot_graph.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_power'|" src/Plot_graph.py
# Update Plot_graph.py result_dir
sed -i "s|result_dir = f'results'|result_dir = f'results_motivation_power'|" src/Plot_graph.py

# Update TierX.yaml sections using helper (preserves other sections like power_constraints, pipelining, etc.)
update_yaml_list TierX.yaml applications "GRU"
update_yaml_list TierX.yaml optimize_metrics "throughput"
update_yaml_list TierX.yaml sweep_types "power"
update_yaml_list TierX.yaml communication_methods "HIGH-BCC"
update_yaml_list TierX.yaml power_sources "SMALL-BAT" "SMALL-CAP"
update_yaml_list TierX.yaml node_placements "NECK-ARM"

echo "Motivation powering changes applied."

# Disable LIGHTWEIGHT mode to save graphs
sed -i "s|LIGHTWEIGHT=true|LIGHTWEIGHT=false|" run.sh

echo "Run run.sh for motivation powering simulation."
./run.sh

# Rollback function
function rollback {
    echo "Rolling back to original files..."
    mv lib/Node/node.py.bak lib/Node/node.py
    mv src/DSE.py.bak src/DSE.py
    mv src/Plot_graph.py.bak src/Plot_graph.py
    mv TierX.yaml.bak TierX.yaml
    mv run.sh.bak run.sh
    echo "Rollback complete."
}

rollback
