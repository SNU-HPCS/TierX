# !/bin/bash

# Parse command-line arguments
GA_ONLY=false
PRUNING_ONLY=false
if [ "$1" = "--ga-only" ]; then
    GA_ONLY=true
    echo "Running GA-only mode (skipping exhaustive searches)"
elif [ "$1" = "--pruning-only" ]; then
    PRUNING_ONLY=true
    echo "Running pruning-only mode (skipping exhaustive and GA searches)"
fi

# measure the wall clock time with milliseconds precision
START_TIME=$(date +%s%3N)

# Initialize timing accumulators
EXHAUSTIVE_TOTAL_MS=0
GA_TOTAL_MS=0
PRUNING_TOTAL_MS=0

# Initialize simulation count accumulators
EXHAUSTIVE_SIM_COUNT=0
GA_SIM_COUNT=0
PRUNING_SIM_COUNT=0
EXHAUSTIVE_SEARCH_SPACE=0
GA_SEARCH_SPACE=0
PRUNING_SEARCH_SPACE=0

# HIGH-BCC_SMALL-BAT
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
  - "node"
communication_methods:
  - "HIGH-BCC"
power_sources:
  - "SMALL-BAT"
node_placements:
  - "NECK-ARM"
  - "NECK-EXTERNAL"
  - "TEMPLE-ARM"
EOL

if [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running GA search..."
    echo "============================================"
    GA_START=$(date +%s%3N)
    DSE_SEARCH="ga" ./run.sh
    GA_END=$(date +%s%3N)
    GA_TOTAL_MS=$((GA_TOTAL_MS + GA_END - GA_START))
    # Capture simulation count for GA
    STATS_JSON=$(DSE_SEARCH="ga" python src/dse_stats.py --json --no-timing --search ga 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('ga_actual_tasks',0) or d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    SPACE=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    GA_SIM_COUNT=$((GA_SIM_COUNT + COUNT))
    GA_SEARCH_SPACE=$((GA_SEARCH_SPACE + SPACE))
fi
if [ "$GA_ONLY" = false ] && [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running EXHAUSTIVE search..."
    echo "============================================"
    EXHAUSTIVE_START=$(date +%s%3N)
    DSE_SEARCH="exhaustive" ./run.sh
    EXHAUSTIVE_END=$(date +%s%3N)
    EXHAUSTIVE_TOTAL_MS=$((EXHAUSTIVE_TOTAL_MS + EXHAUSTIVE_END - EXHAUSTIVE_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="exhaustive" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    EXHAUSTIVE_SIM_COUNT=$((EXHAUSTIVE_SIM_COUNT + COUNT))
    EXHAUSTIVE_SEARCH_SPACE=$((EXHAUSTIVE_SEARCH_SPACE + COUNT))
    echo ""
fi
if [ "$GA_ONLY" = false ]; then
    echo "============================================"
    echo "Running PRUNING search..."
    echo "============================================"
    PRUNING_START=$(date +%s%3N)
    DSE_SEARCH="pruning" ./run.sh
    PRUNING_END=$(date +%s%3N)
    PRUNING_TOTAL_MS=$((PRUNING_TOTAL_MS + PRUNING_END - PRUNING_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="pruning" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    PRUNING_SIM_COUNT=$((PRUNING_SIM_COUNT + COUNT))
    PRUNING_SEARCH_SPACE=$((PRUNING_SEARCH_SPACE + COUNT))
    echo ""
fi
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
  - "power"
communication_methods:
  - "HIGH-BCC"
power_sources:
  - "SMALL-BAT"
  - "SMALL-CAP"
  - "OFF"
node_placements:
  - "NECK-ARM"
EOL

if [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running GA search..."
    echo "============================================"
    GA_START=$(date +%s%3N)
    DSE_SEARCH="ga" ./run.sh
    GA_END=$(date +%s%3N)
    GA_TOTAL_MS=$((GA_TOTAL_MS + GA_END - GA_START))
    # Capture simulation count for GA
    STATS_JSON=$(DSE_SEARCH="ga" python src/dse_stats.py --json --no-timing --search ga 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('ga_actual_tasks',0) or d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    SPACE=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    GA_SIM_COUNT=$((GA_SIM_COUNT + COUNT))
    GA_SEARCH_SPACE=$((GA_SEARCH_SPACE + SPACE))
fi
if [ "$GA_ONLY" = false ] && [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running EXHAUSTIVE search..."
    echo "============================================"
    EXHAUSTIVE_START=$(date +%s%3N)
    DSE_SEARCH="exhaustive" ./run.sh
    EXHAUSTIVE_END=$(date +%s%3N)
    EXHAUSTIVE_TOTAL_MS=$((EXHAUSTIVE_TOTAL_MS + EXHAUSTIVE_END - EXHAUSTIVE_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="exhaustive" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    EXHAUSTIVE_SIM_COUNT=$((EXHAUSTIVE_SIM_COUNT + COUNT))
    EXHAUSTIVE_SEARCH_SPACE=$((EXHAUSTIVE_SEARCH_SPACE + COUNT))
    echo ""
fi
if [ "$GA_ONLY" = false ]; then
    echo "============================================"
    echo "Running PRUNING search..."
    echo "============================================"
    PRUNING_START=$(date +%s%3N)
    DSE_SEARCH="pruning" ./run.sh
    PRUNING_END=$(date +%s%3N)
    PRUNING_TOTAL_MS=$((PRUNING_TOTAL_MS + PRUNING_END - PRUNING_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="pruning" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    PRUNING_SIM_COUNT=$((PRUNING_SIM_COUNT + COUNT))
    PRUNING_SEARCH_SPACE=$((PRUNING_SEARCH_SPACE + COUNT))
    echo ""
fi

# NECK-ARM_SMALL-BAT - sweep 
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
  - "LOW-BCC"
  - "HIGH-BCC"
power_sources:
  - "SMALL-BAT"
node_placements:
  - "NECK-ARM"
EOL

if [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running GA search..."
    echo "============================================"
    GA_START=$(date +%s%3N)
    DSE_SEARCH="ga" ./run.sh
    GA_END=$(date +%s%3N)
    GA_TOTAL_MS=$((GA_TOTAL_MS + GA_END - GA_START))
    # Capture simulation count for GA
    STATS_JSON=$(DSE_SEARCH="ga" python src/dse_stats.py --json --no-timing --search ga 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('ga_actual_tasks',0) or d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    SPACE=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    GA_SIM_COUNT=$((GA_SIM_COUNT + COUNT))
    GA_SEARCH_SPACE=$((GA_SEARCH_SPACE + SPACE))
fi
if [ "$GA_ONLY" = false ] && [ "$PRUNING_ONLY" = false ]; then
    echo "============================================"
    echo "Running EXHAUSTIVE search..."
    echo "============================================"
    EXHAUSTIVE_START=$(date +%s%3N)
    DSE_SEARCH="exhaustive" ./run.sh
    EXHAUSTIVE_END=$(date +%s%3N)
    EXHAUSTIVE_TOTAL_MS=$((EXHAUSTIVE_TOTAL_MS + EXHAUSTIVE_END - EXHAUSTIVE_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="exhaustive" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    EXHAUSTIVE_SIM_COUNT=$((EXHAUSTIVE_SIM_COUNT + COUNT))
    EXHAUSTIVE_SEARCH_SPACE=$((EXHAUSTIVE_SEARCH_SPACE + COUNT))
    echo ""
fi
if [ "$GA_ONLY" = false ]; then
    echo "============================================"
    echo "Running PRUNING search..."
    echo "============================================"
    PRUNING_START=$(date +%s%3N)
    DSE_SEARCH="pruning" ./run.sh
    PRUNING_END=$(date +%s%3N)
    PRUNING_TOTAL_MS=$((PRUNING_TOTAL_MS + PRUNING_END - PRUNING_START))
    # Capture simulation count
    STATS_JSON=$(DSE_SEARCH="pruning" python src/dse_stats.py --json --no-timing 2>/dev/null || echo '{}')
    COUNT=$(echo "$STATS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('counts',{}).get('requested_tasks_total',0))" 2>/dev/null || echo 0)
    PRUNING_SIM_COUNT=$((PRUNING_SIM_COUNT + COUNT))
    PRUNING_SEARCH_SPACE=$((PRUNING_SEARCH_SPACE + COUNT))
    echo ""
fi

echo ""
echo -e "\\033[1;32m                          🦕 TierX ALL DSE Complete! GO HOME... 🦕\\033[0m"
echo -e "\\033[1;38;5;88m                                RAWR! 🦖\\033[0m"
echo ""


# calculate and display total execution time with millisecond precision
END_TIME=$(date +%s%3N)
TOTAL_TIME_MS=$((END_TIME - START_TIME))
TOTAL_SEC=$(awk "BEGIN {printf \"%.3f\", $TOTAL_TIME_MS/1000}")

# Calculate individual search strategy times
EXHAUSTIVE_SEC=$(awk "BEGIN {printf \"%.3f\", $EXHAUSTIVE_TOTAL_MS/1000}")
GA_SEC=$(awk "BEGIN {printf \"%.3f\", $GA_TOTAL_MS/1000}")
PRUNING_SEC=$(awk "BEGIN {printf \"%.3f\", $PRUNING_TOTAL_MS/1000}")

echo -e "\\033[1;36m╔════════════════════════════════════════════════════════════╗\\033[0m"
echo -e "\\033[1;36m║              ⏱️  EXECUTION TIME BREAKDOWN                  ║\\033[0m"
echo -e "\\033[1;36m╠════════════════════════════════════════════════════════════╣\\033[0m"
if [ "$PRUNING_ONLY" = true ]; then
    echo -e "\\033[1;36m║\\033[0m  Pruning Search Time:    \\033[1;35m${PRUNING_SEC}s\\033[0m (pruning-only mode)"
elif [ "$GA_ONLY" = true ]; then
    echo -e "\\033[1;36m║\\033[0m  GA Search Time:         \\033[1;33m${GA_SEC}s\\033[0m (GA-only mode)"
else
    echo -e "\\033[1;36m║\\033[0m  Exhaustive Search Time: \\033[1;32m${EXHAUSTIVE_SEC}s\\033[0m"
    echo -e "\\033[1;36m║\\033[0m  GA Search Time:         \\033[1;33m${GA_SEC}s\\033[0m"
    echo -e "\\033[1;36m║\\033[0m  Pruning Search Time:    \\033[1;35m${PRUNING_SEC}s\\033[0m"
fi
echo -e "\\033[1;36m║\\033[0m  Total Execution Time:   \\033[1;38;5;88m${TOTAL_SEC}s\\033[0m"
echo -e "\\033[1;36m╠════════════════════════════════════════════════════════════╣\\033[0m"
echo -e "\\033[1;36m║              📊 SIMULATION COUNT SUMMARY                   ║\\033[0m"
echo -e "\\033[1;36m╠════════════════════════════════════════════════════════════╣\\033[0m"
if [ "$PRUNING_ONLY" = true ]; then
    echo -e "\\033[1;36m║\\033[0m  Pruning Simulations:    \\033[1;35m${PRUNING_SIM_COUNT}\\033[0m (3 configs)"
    echo -e "\\033[1;36m║\\033[0m  Pruning Search Space:   \\033[0;90m${PRUNING_SEARCH_SPACE}\\033[0m (full search space)"
    if [ "$PRUNING_SEARCH_SPACE" -gt 0 ]; then
        REDUCTION=$(awk "BEGIN {printf \"%.1f\", (1 - $PRUNING_SIM_COUNT / $PRUNING_SEARCH_SPACE) * 100}")
        echo -e "\\033[1;36m║\\033[0m  Pruning Reduction:      \\033[1;35m${REDUCTION}%\\033[0m"
    fi
elif [ "$GA_ONLY" = true ]; then
    echo -e "\\033[1;36m║\\033[0m  GA Simulations:         \\033[1;33m${GA_SIM_COUNT}\\033[0m (3 configs)"
    echo -e "\\033[1;36m║\\033[0m  GA Search Space:        \\033[0;90m${GA_SEARCH_SPACE}\\033[0m (full search space)"
    if [ "$GA_SEARCH_SPACE" -gt 0 ]; then
        REDUCTION=$(awk "BEGIN {printf \"%.1f\", (1 - $GA_SIM_COUNT / $GA_SEARCH_SPACE) * 100}")
        echo -e "\\033[1;36m║\\033[0m  GA Reduction:           \\033[1;35m${REDUCTION}%\\033[0m"
    fi
else
    echo -e "\\033[1;36m║\\033[0m  Exhaustive Simulations: \\033[1;32m${EXHAUSTIVE_SIM_COUNT}\\033[0m (3 configs)"
    echo -e "\\033[1;36m║\\033[0m  GA Simulations:         \\033[1;33m${GA_SIM_COUNT}\\033[0m (3 configs)"
    echo -e "\\033[1;36m║\\033[0m  GA Search Space:        \\033[0;90m${GA_SEARCH_SPACE}\\033[0m (full search space)"
    TOTAL_SIMS=$((EXHAUSTIVE_SIM_COUNT + GA_SIM_COUNT))
    echo -e "\\033[1;36m║\\033[0m  Total Simulations:      \\033[1;38;5;88m${TOTAL_SIMS}\\033[0m"
    if [ "$GA_SEARCH_SPACE" -gt 0 ]; then
            REDUCTION=$(awk "BEGIN {printf \"%.1f\", (1 - $GA_SIM_COUNT / $GA_SEARCH_SPACE) * 100}")
            echo -e "\\033[1;36m║\\033[0m  GA Reduction:           \\033[1;35m${REDUCTION}%\\033[0m"
    fi
fi
echo -e "\\033[1;36m╚════════════════════════════════════════════════════════════╝\\033[0m"
echo ""

# Generate comparison of best solutions across search strategies
echo -e "\\033[1;35m╔════════════════════════════════════════════════════════════════════════════╗\\033[0m"
echo -e "\\033[1;35m║              📊 COMPARISON OF BEST SOLUTIONS BY SEARCH STRATEGY           ║\\033[0m"
echo -e "\\033[1;35m╚════════════════════════════════════════════════════════════════════════════╝\\033[0m"
python src/summarize_best_solutions.py --data-dir data_DSE --compare --save data_DSE/comparison_summary.json 2>/dev/null || echo "    ⚠️  Comparison summary unavailable"
echo ""
