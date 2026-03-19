import yaml
import sys
import numpy as np
import copy

# Possible locations
# brain_deep, brain_shallow, head, chest, waist, wrist, off_body
in_body = ['brain_deep', 'brain_shallow']
on_body = ['head', 'chest', 'waist', 'wrist']
off_body = ['off_body']

# Possible locations
on_implant = ['brain_deep', 'brain_shallow']
near_implant = ['neck', 'temple']
off_implant = ['chest', 'arm', 'external']

def mW_to_dBm(mW):
    return 10 * np.log10(mW)

def dBm_to_mW(dBm):
    return 10 ** (dBm / 10)

# return 1 if match
def find_match(location, src_or_dst, dst_or_src):
    src, dst = location
    if src == src_or_dst and dst == dst_or_src:
        return 1
    elif src == dst_or_src and dst == src_or_dst:
        return 1
    else:
        return 0

def get_path_loss(method, src_location, dst_location, LoS, frequency, posture):
    # path_loss = 60
    use_measured = True
    subject_number = 1
    if use_measured and subject_number == 1:
        # Data of subject 1
        location = src_location, dst_location
        if frequency == 10:
            assert method == 'BCC' or method == 'BCP', "Invalid frequency for BCC"
            assert src_location != 'external' and dst_location != 'external', "Invalid locations for frequency 10 MHz BCC"
            if find_match(location, 'brain_shallow', 'temple'): # 10cm
                path_loss = 30.821435
            elif find_match(location, 'brain_shallow', 'neck'): # 40cm
                path_loss = 40.13466765
            elif find_match(location, 'neck', 'arm'):
                if posture == 'sitting':
                    path_loss = 44.30635765
                elif posture == 'standing':
                    path_loss = 43.21158186
            elif find_match(location, 'neck', 'chest'):
                if posture == 'sitting':
                    path_loss = 46.73340704
                elif posture == 'standing':
                    path_loss = 46.61986438
            elif find_match(location, 'temple', 'arm'):
                if posture == 'sitting':
                    path_loss = 45.44383552
                elif posture == 'standing':
                    path_loss = 45.39544604
            elif find_match(location, 'temple', 'chest'):
                if posture == 'sitting':
                    path_loss = 46.75048172
                elif posture == 'standing':
                    path_loss = 43.89262067
            else:
                assert False, f"Invalid locations for frequency 10 MHz BCC: {location}"
        elif frequency == 40:
            assert method == 'BCC' or method == 'BCP', "Invalid frequency for BCC"
            assert src_location != 'external' and dst_location != 'external', "Invalid locations for frequency 40 MHz BCC"
            if find_match(location, 'brain_shallow', 'temple'): # 10cm
                path_loss = 23.89621517
            elif find_match(location, 'brain_shallow', 'neck'): # 40cm
                path_loss = 37.13579935
            elif find_match(location, 'neck', 'arm'):
                if posture == 'sitting':
                    path_loss = 44.22766632
                elif posture == 'standing':
                    path_loss = 41.5921031
            elif find_match(location, 'neck', 'chest'):
                if posture == 'sitting':
                    path_loss = 41.69449198
                elif posture == 'standing':
                    path_loss = 39.36034253
            elif find_match(location, 'temple', 'arm'):
                if posture == 'sitting':
                    path_loss = 43.59768496
                elif posture == 'standing':
                    path_loss = 41.64121784
            elif find_match(location, 'temple', 'chest'):
                if posture == 'sitting':
                    path_loss = 38.88180193
                elif posture == 'standing':
                    path_loss = 35.67553417
            else:
                assert False, f"Invalid locations for frequency 40 MHz BCC: {location}"
        elif frequency == 900:
            assert method == 'RF', "Invalid frequency for RF"
            if find_match(location, 'brain_shallow', 'temple'): # 10cm
                path_loss = 30.821435
            elif find_match(location, 'brain_shallow', 'neck'): # 40cm
                path_loss = 40.13466765
            elif find_match(location, 'neck', 'arm'):
                if posture == 'sitting':
                    path_loss = 36.6
                elif posture == 'standing':
                    path_loss = 38.6
            elif find_match(location, 'neck', 'chest'):
                if posture == 'sitting':
                    path_loss = 47
                elif posture == 'standing':
                    path_loss = 49.6
            elif find_match(location, 'temple', 'arm'):
                if posture == 'sitting':
                    path_loss = 38.2
                elif posture == 'standing':
                    path_loss = 39.2
            elif find_match(location, 'temple', 'chest'):
                if posture == 'sitting':
                    path_loss = 41.6
                elif posture == 'standing':
                    path_loss = 44.8
            elif find_match(location, 'neck', 'external'):
                if posture == 'sitting':
                    if LoS:
                        path_loss = 55.6
                    else:
                        path_loss = 56.6
                elif posture == 'standing':
                    if LoS:
                        path_loss = 58.2
                    else:
                        path_loss = 60.4
            elif find_match(location, 'temple', 'external'):
                if posture == 'sitting':
                    if LoS:
                        path_loss = 52.4
                    else:
                        path_loss = 52.8
                elif posture == 'standing':
                    if LoS:
                        path_loss = 50.6
                    else:
                        path_loss = 53.0
            else:
                assert False, f"Invalid locations for frequency 900 MHz RF: {location}"
        elif frequency == 13:
            assert method == 'Inductive', "Invalid frequency for Inductive"
            path_loss = 10
        else:
            assert 0, "Invalid frequency for Inductive"
            path_loss = 0
    else:
        # synthetic path loss
        if frequency <= 20:
            path_loss = 10
        elif frequency <= 100:
            path_loss = 30
        elif frequency == 400:
            path_loss = 40  # Default path loss for 400 MHz
        elif frequency == 900:
            path_loss = 50  # Default path loss for 900 MHz
        elif frequency == 2400:
            path_loss = 60  # Default path loss for 2400 MHz
        elif frequency >= 3000 and frequency <= 10000:
            path_loss = 70
        else:
            path_loss = 60  # Default path loss for unsupported frequencies

        
        if src_location in in_body and dst_location in in_body:
            path_loss += 0
        elif (src_location in in_body and dst_location in on_body) or (src_location in on_body and dst_location in in_body):
            path_loss += 0
        elif (src_location in in_body and dst_location in off_body) or (src_location in off_body and dst_location in in_body):
            path_loss += 0
        elif src_location in on_body and dst_location in on_body:
            path_loss += 0
        elif (src_location in on_body and dst_location in off_body) or (src_location in off_body and dst_location in on_body):
            path_loss += 8
        elif src_location in off_body and dst_location in off_body:
            path_loss += 0
        else:
            assert 0, "Invalid locations for frequency 900 MHz RF"
            path_loss += 0


    # Reference for RF)
    # Channel Model for Body Area Network (BAN)
    # if method == 'RF':
    #     if frequency == 400:
    #         if src_location == 'brain_deep' and dst_location == 'brain_deep':
    #             # Get path loss of reference distance d0
    #             d0 = 0.05 # in m
    #             path_loss_d0, n, std_dev = 35.04, 6.26, 8.18
    #             path_loss = path_loss_d0 + 10 * n * np.log10(distance / d0)
    #         elif src_location == 'brain_deep' and dst_location == 'brain_shallow':
    #             d0 = 0.05
    #             path_loss_d0, n, std_dev = 40.94, 4.99, 9.05
    #             path_loss = path_loss_d0 + 10 * n * np.log10(distance / d0)
    #         elif src_location == 'brain_deep' and dst_location == 'head':
    #             d0 = 0.05
    #             path_loss_d0, n, std_dev = 47.14, 4.26, 7.85
    #             path_loss = path_loss_d0 + 10 * n * np.log10(distance / d0)
    #         elif src_location == 'brain_shallow' and dst_location == 'head':
    #             d0 = 0.05
    #             path_loss_d0, n, std_dev = 49.81, 4.22, 6.81
    #             path_loss = path_loss_d0 + 10 * n * np.log10(distance / d0)
    #         elif src_location in {} and dst_location not in {'brain_deep', 'brain_shallow', 'off_body'}:
    #             if atmosphere == 'indoor':
    #                 a, b, std_dev = 3, 34.6, 4.63
    #                 path_loss = a * np.log10(distance) + b
    #             elif atmosphere == 'anechoic':
    #                 a, b, std_dev = 22.6, -7.85, 5.60
    #                 path_loss = a * np.log10(distance) + b
    #         else:
    #             print("No path loss model")
    #             print(f'Method: {method}, Frequency: {frequency}, Atmosphere: {atmosphere}, Src: {src_location}, Dst: {dst_location}')
    #             # sys.exit()
    #     elif frequency == 600:
    #         if src_location in on_body and dst_location in on_body:
    #             if atmosphere == 'indoor':
    #                 a, b, std_dev = 16.7, -0.45, 5.99
    #                 path_loss = a * np.log10(distance) + b
    #             elif atmosphere == 'anechoic':
    #                 a, b, std_dev = 17.2, 1.61, 6.96
    #                 path_loss = a * np.log10(distance) + b
    #         else:
    #             print("No path loss model")
    #             print(f'Method: {method}, Frequency: {frequency}, Atmosphere: {atmosphere}, Src: {src_location}, Dst: {dst_location}')
    #             # sys.exit()
    #     elif frequency == 900:
    #         if src_location in on_body and dst_location in on_body:
    #             if atmosphere == 'indoor':
    #                 a, b, std_dev = 15.5, 5.38, 5.35
    #                 path_loss = a * np.log10(distance) + b
    #             elif atmosphere == 'anechoic':
    #                 a, b, std_dev = 28.8, -23.5, 11.7
    #                 path_loss = a * np.log10(distance) + b
    #         elif src_location in on_body and dst_location == 'off_body':
    #             if src_location == 'chest' or src_location == 'head':
    #                 if LoS:
    #                     if distance == 1:
    #                         path_loss = 43.51
    #                     elif distance == 2:
    #                         path_loss = 52.73
    #                     elif distance == 3:
    #                         path_loss = 54.6
    #                     elif distance == 4:
    #                         path_loss = 49.78
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #                 else: # No LoS
    #                     if distance == 1:
    #                         path_loss = 60.13
    #                     elif distance == 2:
    #                         path_loss = 56.34
    #                     elif distance == 3:
    #                         path_loss = 64.67
    #                     elif distance == 4:
    #                         path_loss = 58.67
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #             elif src_location == 'waist' or src_location == 'wrist':
    #                 if LoS:
    #                     if distance == 1:
    #                         path_loss = 47.84
    #                     elif distance == 2:
    #                         path_loss = 65.29
    #                     elif distance == 3:
    #                         path_loss = 65.72
    #                     elif distance == 4:
    #                         path_loss = 70.72
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #                 else: # No LoS
    #                     if distance == 1:
    #                         path_loss = 60.27
    #                     elif distance == 2:
    #                         path_loss = 65.6
    #                     elif distance == 3:
    #                         path_loss = 70.94
    #                     elif distance == 4:
    #                         path_loss = 74.43
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #         else:
    #             print("No path loss model")
    #             print(f'Method: {method}, Frequency: {frequency}, Atmosphere: {atmosphere}, Src: {src_location}, Dst: {dst_location}')
    #             # sys.exit()
    #     elif frequency == 2400:
    #         if src_location in in_body and dst_location in in_body:
    #             # Reference: A Communication Link Analysis Based on Biological Implant Wireless Body Area Networks
    #             d0 = 0.005 # in m
    #             path_loss_d0, n, std_dev = 37.97, 1.631, 0.658
    #             path_loss = path_loss_d0 + 10 * n * np.log10(distance)
    #         elif src_location in on_body and dst_location in on_body:
    #             if atmosphere == 'indoor':
    #                 a, b, std_dev = 6.6, 36.1, 3.8
    #                 path_loss = a * np.log10(distance) + b
    #             elif atmosphere == 'anechoic':
    #                 a, b, std_dev = 29.3, -16.8, 6.89
    #                 path_loss = a * np.log10(distance) + b
    #         elif (src_location in on_body or src_location in in_body) and dst_location == 'off_body':
    #             if src_location == 'chest' or src_location == 'head':
    #                 if LoS:
    #                     if distance == 1:
    #                         path_loss = 53.81
    #                     elif distance == 2:
    #                         path_loss = 53.12
    #                     elif distance == 3:
    #                         path_loss = 56.04
    #                     elif distance == 4:
    #                         path_loss = 64.72
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #                 else:
    #                     if distance == 1:
    #                         path_loss = 61.81
    #                     elif distance == 2:
    #                         path_loss = 68.64
    #                     elif distance == 3:
    #                         path_loss = 60.12
    #                     elif distance == 4:
    #                         path_loss = 63.1
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #             elif src_location == 'waist' or src_location == 'wrist':
    #                 if LoS:
    #                     if distance == 1:
    #                         path_loss = 59.78
    #                     elif distance == 2:
    #                         path_loss = 73.04
    #                     elif distance == 3:
    #                         path_loss = 67.2
    #                     elif distance == 4:
    #                         path_loss = 63.61
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #                 else:
    #                     if distance == 1:
    #                         path_loss = 69.3
    #                     elif distance == 2:
    #                         path_loss = 72.62
    #                     elif distance == 3:
    #                         path_loss = 73.72
    #                     elif distance == 4:
    #                         path_loss = 74.4
    #                     else:
    #                         print("Invalid distance")
    #                         sys.exit()
    #         else:
    #             print("No path loss model")
    #             print(f'Method: {method}, Frequency: {frequency}, Atmosphere: {atmosphere}, Src: {src_location}, Dst: {dst_location}')
    #             # sys.exit()
    #     elif frequency >= 3000 and frequency <= 10000:
    #         if src_location in in_body and dst_location == 'head':
    #             # Reference: Experimental Ultra Wideband Path Loss Models for Implant Communications
    #             path_loss = 30.8 + 52 * distance
    #         if src_location in in_body and dst_location == 'off_body':
    #             # Reference: Experimental Ultra Wideband Path Loss Models for Implant Communications
    #             d0 = 0.01 # in m
    #             path_loss_d0, n = 70.4, 0.7
    #             path_loss = 70.4 + 10 * n * np.log10(distance/d0)
    #         if src_location in on_body and dst_location in on_body:
    #             if atmosphere == 'indoor':
    #                 a, b, std_dev = 19.2, 3.38, 4.4
    #                 path_loss = a * np.log10(distance) + b
    #             elif atmosphere == 'anechoic':
    #                 a, b, std_dev = 34.1, -31.4, 4.85
    #                 path_loss = a * np.log10(distance) + b
    #         else:
    #             print("No path loss model")
    #             print(f'Method: {method}, Frequency: {frequency}, Atmosphere: {atmosphere}, Src: {src_location}, Dst: {dst_location}')
    #             # sys.exit()
    #     else:
    #         print("Frequency not supported")
    #         sys.exit()

    # # Reference for BCC, BCP)
    # # The signal transmission mechanism on the surface of human body for body channel communication
    # elif method in {'BCC', 'BCP'}:
    #     # Approximated from the paper Fig. 12
    #     A = 0.05 * 10 / frequency
    #     B = 0.15 / 100 * frequency
    #     C = 4.5 / 100 * frequency
    #     path_loss = A * 1 / (distance ** 3) + B * np.exp(-C * distance) / distance
    #     # to dB
    #     path_loss = -10 * np.log10(path_loss)
    # # Reference for Inductive)
    # # Semi-Implantable Wireless Power Transfer (WPT) System Integrated With On-Chip Power Management Unit (PMU) for Neuromodulation Application
    # elif method == 'Inductive':
    #     # Calculate path loss
    #     if src_location == 'head' and dst_location in in_body:
    #         d0 = 0.06
    #         if distance < d0:
    #             path_loss = -10 * np.log10(0.303)
    #         else:
    #             print("Invalid distance")
    #             sys.exit()

    return path_loss

class PropagationChannel():
    def __init__(self, node1, node2, env):
        self.node1 = node1
        self.node2 = node2
        self.env = env
        
        self.propagation_latency = 0

    def propagate(self, src_node, dst_node, type):
        print("Propagating signals from", src_node.name, "to", dst_node.name)
        signal_blocks = src_node.data_blocks if type == 'data' else src_node.power_blocks
        for trial in signal_blocks:
            for signal in trial:
                # Calculate path loss between node1 & node2
                # Signal can be data or power
                # Get method, LoS, distance, and frequency
                method = signal.method
                frequency = signal.frequency
                posture = self.env['posture']
                
                channels = self.env['channels']
                valid_channel = False
                for channel in channels:
                    if (channel['src'], channel['dst']) == (src_node.name, dst_node.name) or \
                        (channel['src'], channel['dst']) == (dst_node.name, src_node.name):
                        LoS = channel['LoS']
                        src_location = src_node.location
                        dst_location = dst_node.location
                        valid_channel = True
                        break
                if not valid_channel:
                    print("Exit: No valid channel found between", src_node.name, "and", dst_node.name)
                    sys.exit()
                path_loss_dB = get_path_loss(method, src_location, dst_location, LoS, frequency, posture)
                # sys.__stdout__.write(f'{path_loss_dB} dB path loss from {src_node.name} to {dst_node.name}\n')
                transmitted_power_dBm = mW_to_dBm(signal.signal_power)
                # sys.__stdout__.write(f'Transmitted power: {transmitted_power_dBm} dBm\n')
                received_power_dBm = transmitted_power_dBm - path_loss_dB
                signal.signal_power = dBm_to_mW(received_power_dBm)
        
        if type == 'data':
            dst_node.data_blocks = copy.deepcopy(signal_blocks)
            src_node.data_blocks = []
        else:
            dst_node.power_blocks = copy.deepcopy(signal_blocks)
            src_node.power_blocks = []

        return self.propagation_latency
