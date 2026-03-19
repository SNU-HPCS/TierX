#!/bin/bash
# setup_motivation_communication.sh
# Apply motivation communication changes, then roll back to original
# Uses sed to modify only necessary values, preserving other settings like pipelining

set -e

# Source yaml update functions
source lib/yaml_update.sh

# Backup originals
cp src/DSE.py src/DSE.py.bak
cp src/Plot_graph.py src/Plot_graph.py.bak
cp TierX.yaml TierX.yaml.bak
cp run.sh run.sh.bak

echo "Applying motivation communication changes..."

# Update DSE.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_communication'|" src/DSE.py
# Update Plot_graph.py data_dir
sed -i "s|data_dir = 'data_DSE'|data_dir = 'data_DSE_motivation_communication'|" src/Plot_graph.py
# Update Plot_graph.py result_dir
sed -i "s|result_dir = f'results'|result_dir = f'results_motivation_communication'|" src/Plot_graph.py


# Update TierX.yaml sections using helper (preserves other sections like power_constraints, pipelining, etc.)
update_yaml_list TierX.yaml applications "Seizure"
update_yaml_list TierX.yaml optimize_metrics "latency"
update_yaml_list TierX.yaml sweep_types "communication"
update_yaml_list TierX.yaml communication_methods "HIGH-BCC" "LOW-RF"
update_yaml_list TierX.yaml power_sources "OFF"
update_yaml_list TierX.yaml node_placements "NECK-ARM"

echo "Motivation communication changes applied."

# Disable LIGHTWEIGHT mode to save graphs
sed -i "s|LIGHTWEIGHT=true|LIGHTWEIGHT=false|" run.sh

echo "Run run.sh for motivation communication simulation."
./run.sh

# Rollback function
function rollback {
    echo "Rolling back to original files..."
    mv src/DSE.py.bak src/DSE.py
    mv src/Plot_graph.py.bak src/Plot_graph.py
    mv TierX.yaml.bak TierX.yaml
    mv run.sh.bak run.sh
    echo "Rollback complete."
}

rollback
