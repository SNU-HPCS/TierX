#!/bin/bash
# restore_all_bak.sh
# Restore all .bak files in the current directory and subdirectories
# 
# WARNING: This script restores ALL .bak files including TierX.yaml.bak
# If you have made modifications to TierX.yaml (e.g., pipelining, power_constraints),
# those changes will be lost when restoring from backup.
#
# To preserve custom TierX.yaml settings, consider using:
#   git checkout HEAD -- TierX.yaml
# instead of restoring from .bak file

echo "⚠️  WARNING: Restoring .bak files will overwrite current files."
echo "   If you have custom TierX.yaml settings (pipelining, power_constraints, etc.),"
echo "   consider using 'git checkout HEAD -- TierX.yaml' instead."
echo ""

find . -type f -name '*.bak' | while read bakfile; do
    origfile="${bakfile%.bak}"
    echo "Restoring $bakfile to $origfile"
    mv "$bakfile" "$origfile"
done

echo "All .bak files restored."
