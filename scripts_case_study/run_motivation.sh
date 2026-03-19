#!/bin/bash
# run_motivation.sh
# Run all setup_motivation_* scripts sequentially

# set -e


declare -A status
scripts=(setup_motivation_*.sh)

for script in "${scripts[@]}"; do
    echo "Running $script..."
    bash "$script"
    if [ $? -eq 0 ]; then
        status["$script"]="SUCCESS"
    else
        status["$script"]="FAILURE"
    fi
    echo "Finished $script."
done

echo -e "\nSummary:"
for script in "${scripts[@]}"; do
    echo "$script: ${status[$script]}"
done
