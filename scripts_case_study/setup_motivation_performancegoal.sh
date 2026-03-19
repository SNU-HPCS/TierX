#!/bin/bash
# setup_motivation_performancegoal.sh
# Set up TierX.yaml and create_components.py for performance goal motivation and run simulation
# Uses sed to modify only necessary values, preserving other settings like pipelining

set -e

# Source yaml update functions
source lib/yaml_update.sh

cp TierX.yaml TierX.yaml.bak
cp src/create_components.py src/create_components.py.bak
cp run.sh run.sh.bak
cp src/DSE.py src/DSE.py.bak
cp src/Plot_graph.py src/Plot_graph.py.bak

echo "Applying motivation performance goal changes..."

# Update DSE.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_performancegoal'|" src/DSE.py
# Update Plot_graph.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_performancegoal'|" src/Plot_graph.py
# Update Plot_graph.py result_dir
sed -i "s|result_dir = f'results'|result_dir = f'results_motivation_performancegoal'|" src/Plot_graph.py

# Update TierX.yaml sections using helper (preserves other sections like power_constraints, pipelining, etc.)
update_yaml_list TierX.yaml applications "Seizure"
update_yaml_list TierX.yaml optimize_metrics "throughput"
update_yaml_list TierX.yaml sweep_types "power"
update_yaml_list TierX.yaml communication_methods "HIGH-BCC"
update_yaml_list TierX.yaml power_sources "SMALL-BAT" "SMALL-CAP"
update_yaml_list TierX.yaml node_placements "NECK-ARM"

echo "Motivation performance goal (1/2) changes applied."

# Disable LIGHTWEIGHT mode to save graphs
sed -i "s|LIGHTWEIGHT=true|LIGHTWEIGHT=false|" run.sh

echo "Run run.sh for motivation performance goal simulation (1/2)."
./run.sh

# DSE.py: change dimensions
# before: dimensions = [[num_elec * 6, 1], [num_elec * 6, 1], [num_elec * 6, 1], [32, 1], [1, 1], [1, 1]]
# after: dimensions = [[num_elec * 6, 1], [num_elec * 6, 1], [num_elec * 6, 1], [256, 1], [1, 1], [1, 1]]
sed -i "s/dimensions = \[\[num_elec \* 6, 1\], \[num_elec \* 6, 1\], \[num_elec \* 6, 1\], \[32, 1\], \[1, 1\], \[1, 1\]\]/dimensions = \[\[num_elec \* 6, 1\], \[num_elec \* 6, 1\], \[num_elec \* 6, 1\], \[256, 1\], \[1, 1\], \[1, 1\]\]/" src/DSE.py

# before: scaling_factor = [num_elec, num_elec, num_elec, num_elec, 32, 1]
# after: scaling_factor = [num_elec, num_elec, num_elec, num_elec, 256, 1]
sed -i "s/scaling_factor = \[num_elec, num_elec, num_elec, num_elec, 32, 1\]/scaling_factor = \[num_elec, num_elec, num_elec, num_elec, 256, 1\]/" src/DSE.py


# Update TierX.yaml for second run (only change optimize_metrics)
update_yaml_list TierX.yaml optimize_metrics "operatingtime"

echo "Motivation performance goal (2/2) changes applied."

echo "Run run.sh for motivation performance goal simulation (2/2)."
./run.sh


function rollback {
    echo "Rolling back to original files..."
    mv TierX.yaml.bak TierX.yaml
    mv src/create_components.py.bak src/create_components.py
    mv run.sh.bak run.sh
    mv src/DSE.py.bak src/DSE.py
    mv src/Plot_graph.py.bak src/Plot_graph.py
    echo "Rollback complete."
}

rollback