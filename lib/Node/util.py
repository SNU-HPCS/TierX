# common utilities for TierX

def check_overlap(start1, end1, start2, end2):
    if start1 >= start2 and start1 < end2:
        return True
    if start2 >= start1 and start2 < end1:
        return True
    return False