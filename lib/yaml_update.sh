#!/bin/bash
# yaml_update.sh - Helper functions to update specific YAML sections without overwriting the entire file
# Usage: source lib/yaml_update.sh

# Update a YAML list section (replaces all items under a key)
# Arguments:
#   $1: YAML file path
#   $2: Section name (e.g., "applications", "optimize_metrics")
#   $3+: New values (e.g., "Seizure" "SpikeSorting")
#
# Example: update_yaml_list TierX.yaml applications "Seizure" "SpikeSorting"
update_yaml_list() {
    local file="$1"
    local section="$2"
    shift 2
    local values=("$@")
    
    # Create temporary file
    local tmpfile=$(mktemp)
    
    # Use awk to replace the section
    awk -v section="$section" -v values="${values[*]}" '
    BEGIN {
        in_section = 0
        # Split values by space
        n = split(values, arr, " ")
    }
    {
        # Check if this is the target section header
        if ($0 ~ "^" section ":") {
            print $0
            in_section = 1
            # Print new values
            for (i = 1; i <= n; i++) {
                print "  - \"" arr[i] "\""
            }
            next
        }
        
        # If we are in the target section, skip old list items
        if (in_section) {
            # Check if line starts with "  - " (list item)
            if ($0 ~ /^  - /) {
                next  # Skip old list items
            }
            # Check if line is empty or starts a new section (not indented or different key)
            if ($0 ~ /^[^ ]/ || $0 ~ /^$/ || $0 ~ /^#/) {
                in_section = 0
                print $0
                next
            }
            # Skip any other indented content under the section
            next
        }
        
        print $0
    }
    ' "$file" > "$tmpfile"
    
    mv "$tmpfile" "$file"
}

# Update a single YAML scalar value
# Arguments:
#   $1: YAML file path
#   $2: Key path (supports simple keys only, e.g., "enabled" under section)
#   $3: New value
#
# For nested keys, use sed directly
update_yaml_scalar() {
    local file="$1"
    local key="$2"
    local value="$3"
    
    # Simple replacement for top-level scalar
    sed -i "s/^${key}:.*/${key}: ${value}/" "$file"
}

echo "YAML update functions loaded."
