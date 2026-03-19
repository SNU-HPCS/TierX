#!/bin/bash
# setup_motivation_node_placement.sh
# Apply motivation node placement changes, then roll back to original
# Uses sed to modify only necessary values, preserving other settings like pipelining

set -e

# Source yaml update functions
source lib/yaml_update.sh

# Backup originals
cp lib/Node/node.py lib/Node/node.py.bak
cp src/create_components.py src/create_components.py.bak
cp src/DSE.py src/DSE.py.bak
cp src/Plot_graph.py src/Plot_graph.py.bak
cp TierX.yaml TierX.yaml.bak
cp run.sh run.sh.bak

echo "Applying motivation node placement changes..."

# node.py: set power_constraint
sed -i "s/self.power_constraint = 200 # in mW/self.power_constraint = 15 # in mW/" lib/Node/node.py

# create_components.py: change latency, static, dynamic for 'off'
sed -i "s/latency \*= 0.5/latency *= 0.25/; s/static \*= 2/static *= 4/; s/dynamic \*= 2/dynamic *= 4/" src/create_components.py

# DSE.py: change output_latency, dynamic_power, static_power
sed -i "s/processor\\['output_latency'\\][[:space:]]*\\*=[[:space:]]*0.2/processor['output_latency'] *= 0.02/; s/processor\\['dynamic_power'\\][[:space:]]*\\*=[[:space:]]*5/processor['dynamic_power'] *= 50/; s/processor\\['static_power'\\][[:space:]]*\\*=[[:space:]]*5/processor['static_power'] *= 50/" src/DSE.py

# DSE.py: change dimensions
# before: dimensions = [[num_elec, 1], [256, 1], [256, 1], [256, 1], [256, 1], [256, 1], [2, 1]]
# after: dimensions = [[num_elec, 1], [num_elec/2, 1], [num_elec/2, 1], [num_elec/2, 1], [num_elec/2, 1], [num_elec/2, 1], [2, 1]]
sed -i "s/dimensions = \[\[num_elec, 1\], \[256, 1\], \[256, 1\], \[256, 1\], \[256, 1\], \[256, 1\], \[2, 1\]\]/dimensions = \[\[num_elec, 1\], \[num_elec\/2, 1\], \[num_elec\/2, 1\], \[num_elec\/2, 1\], \[num_elec\/2, 1\], \[num_elec\/2, 1\], \[2, 1\]\]/" src/DSE.py

# before: scaling_factor = [num_elec, num_elec, 256, 256, 256, 256, 256]
# after: scaling_factor = [num_elec, num_elec, num_elec/2, num_elec/2, num_elec/2, num_elec/2, num_elec/2]
sed -i "s/scaling_factor = \[num_elec, num_elec, 256, 256, 256, 256, 256\]/scaling_factor = \[num_elec, num_elec, num_elec\/2, num_elec\/2, num_elec\/2, num_elec\/2, num_elec\/2\]/" src/DSE.py


# Update DSE.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_node'|" src/DSE.py
# Update Plot_graph.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_node'|" src/Plot_graph.py
# Update Plot_graph.py result_dir
sed -i "s|result_dir = f'results'|result_dir = f'results_motivation_node'|" src/Plot_graph.py

# Update TierX.yaml sections using helper (preserves other sections like power_constraints, pipelining, etc.)
update_yaml_list TierX.yaml applications "NN"
update_yaml_list TierX.yaml optimize_metrics "throughput"
update_yaml_list TierX.yaml sweep_types "node"
update_yaml_list TierX.yaml communication_methods "LOW-RF"
update_yaml_list TierX.yaml power_sources "OFF"
update_yaml_list TierX.yaml node_placements "NECK-ARM" "NECK-EXTERNAL"


echo "Motivation node placement changes applied."

# Disable LIGHTWEIGHT mode to save graphs
sed -i "s|LIGHTWEIGHT=true|LIGHTWEIGHT=false|" run.sh

echo "Run run.sh for motivation node placement simulation."
./run.sh

# Rollback function
function rollback {
    echo "Rolling back to original files..."
    mv lib/Node/node.py.bak lib/Node/node.py
    mv src/create_components.py.bak src/create_components.py
    mv src/DSE.py.bak src/DSE.py
    mv src/Plot_graph.py.bak src/Plot_graph.py
    mv TierX.yaml.bak TierX.yaml
    mv run.sh.bak run.sh
    echo "Rollback complete."
}

rollback