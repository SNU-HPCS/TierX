#!/bin/bash

# Update TierX.yaml with the specified values
cat > TierX.yaml <<EOL
applications:
  - "NN"
  - "Seizure"
  - "SpikeSorting"
  - "GRU"
optimize_metrics:
  - "latency"
  - "operatingtime"
  - "throughput"
sweep_types:
  - "communication"
communication_methods:
  - "LOW-RF"
power_sources:
  - "SMALL-BAT"
node_placements:
  - "NECK-EXTERNAL"
EOL

# Run the simulation script with the specified options
./run.sh -l
./run.sh -p
./run.sh
