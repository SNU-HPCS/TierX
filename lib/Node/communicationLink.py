import yaml
import sys
import numpy as np
import math
import Node.util as util
from scipy.special import erfcinv, erfc
sys.path.append('..')
from profiler import ADD_STATS, UPDATE_STATS

debug_ecc = False

def mW_to_dBm(mW):
    return 10 * np.log10(mW)

def dBm_to_mW(dBm):
    return 10 ** (dBm / 10)

def get_rx_noise(thermal_noise_floor, bandwidth, noise_figure):
    term_1 = dBm_to_mW(thermal_noise_floor + 10 * np.log10(bandwidth * 1e6) + noise_figure)
    EMI_power = -73.27846704 # in dBm
    term_2 = dBm_to_mW(EMI_power)
    quatization_step = 0.00394 # in V
    R = 10000 # in Ohms
    scope_power = (quatization_step**2)/12/R
    term_3 = scope_power * 1e3 # convert to mW
    return mW_to_dBm(term_1 + term_2 + term_3)

def get_BER(signal, rx):
    # Calculate SNR of signal
    noise_floor = -174 # in dBm/Hz
    rx_noise_dBm = get_rx_noise(noise_floor, signal.bandwidth, rx.noise_figure)
    # sys.__stdout__.write(f'RX noise: {rx_noise_dBm} dBm\n')
    signal_power_dBm = mW_to_dBm(signal.signal_power)
    # sys.__stdout__.write(f'Signal power: {signal_power_dBm} dBm\n')
    SNR_dB = signal_power_dBm - rx_noise_dBm
    SNR_linear = dBm_to_mW(SNR_dB)
    SNR_per_bit = SNR_linear * signal.bandwidth / signal.data_rate
    # print('SNR per bit in dB:', 10 * np.log10(SNR_per_bit))
    
    # Calculate BER
    if signal.modulation == 'BPSK':
        BER = 0.5 * erfc(np.sqrt(SNR_per_bit))
    elif signal.modulation == 'BFSK':
        BER = 0.5 * erfc(np.sqrt(SNR_per_bit))
    elif signal.modulation == 'OOK':
        BER = 0.5 * erfc(np.sqrt(SNR_per_bit / 4))
    else:
        print('Exit: Modulation not recognized')
        sys.exit()
    
    return BER
class CommunicationLink():
    def __init__(self, node):
        self.node = node
        args = node.args['comm_link']

        self.static_power = 0
        self.dynamic_power = 0
        if args['transceivers'] is not None:
            trx_list = [self.Transceiver(node, trx) for trx in args['transceivers']]
            self.tx, self.rx = [], []
            for trx in trx_list:
                self.static_power += trx.static_power
                if trx.type in {'TX', 'TRX'}:
                    self.tx.append(trx)
                if trx.type in {'RX', 'TRX'}:
                    self.rx.append(trx)
                if trx.type not in {'TX', 'RX', 'TRX'}:
                    print('Exit: Transceiver type not recognized')
                    sys.exit()
        
        self.ecc = None
        self.compression = None
        self.protocol = None

        if args['protocol']:
            self.protocol = self.ProtocolProcessor(args['protocol'])
            self.static_power += self.protocol.static_power
        if args['compression']:
            self.compression = self.CompressionEngine(args['compression'])
            self.static_power += self.compression.static_power
        if args['error_correction']:
            self.ecc = self.ErrorCorrectionUnit(args['error_correction'])
            self.static_power += self.ecc.static_power

    class Transceiver():
        def __init__(self, node, args):
            self.type = args['type']
            self.method = args['method']
            self.frequency = args['frequency']
            self.bandwidth = args['bandwidth']
            self.data_rate = args['data_rate']
            self.modulation = args['modulation']
            self.noise_figure = args['noise_figure']
            self.static_power = args['static_power']
            self.dynamic_power = args['dynamic_power']

            if self.type in {'TX', 'TRX'}:
                self.radiated_power = args['radiated_power']
        
        def transmit_data(self, data, send_time):
            # Transmit data wirelessly
            data.wireless = True
            data.method = self.method
            data.modulation = self.modulation
            data.signal_power = self.radiated_power
            data.frequency = self.frequency
            data.bandwidth = self.bandwidth
            data.data_rate = self.data_rate # in Mbps
            data.start_time = send_time
            
            latency = data.size / self.data_rate * 1e-3 # in ms
            data.duration = latency
            return latency
        
        def receive_data(self, data):
            # Receive wireless data
            data.wireless = False
            data.input_from = 'comm_link'

            latency = data.size / self.data_rate * 1e-3
            return latency

        def check_TRX_interference(self, other_trx_list):
            for other_trx in other_trx_list:
                # Check if two transceivers interfere with each other
                # 1) Check if they are on the same frequency
                my_freq_band = (self.frequency - self.bandwidth / 2, self.frequency + self.bandwidth / 2)
                other_freq_band = (other_trx.frequency - other_trx.bandwidth / 2, other_trx.frequency + other_trx.bandwidth / 2)
                if util.check_overlap(my_freq_band[0], my_freq_band[1], other_freq_band[0], other_freq_band[1]) and self.method == other_trx.method:
                    return True
            return False

        def check_TRX_match(self, other_trx):
            if self.method == other_trx.method and self.frequency == other_trx.frequency and self.bandwidth == other_trx.bandwidth \
            and self.data_rate == other_trx.data_rate and self.modulation == other_trx.modulation:
                return True
            return False

    class CompressionEngine():
        def __init__(self, args):
            self.compression_ratio = args['compression_ratio']
            self.lossy = args['lossy']
            self.latency = args['latency']
            self.static_power = args['static_power']
            self.dynamic_power = args['dynamic_power']
        
        def compress_data(self, data):
            data.compressed = True
            data.size = data.size / self.compression_ratio

            if self.lossy:
                pass
            return self.latency
        
        def decompress_data(self, data):
            data.compress = False
            data.size = data.size * self.compression_ratio
            return self.latency
    
    class ProtocolProcessor():
        def __init__(self, args):
            self.packet_payload = args['packet_payload']
            self.packet_header = args['packet_header']
            self.latency = args['latency']
            self.static_power = args['static_power']
            self.dynamic_power = args['dynamic_power']
        
        def pack_data(self, data):
            # Add header to data payload
            num_packets = (data.size + self.packet_payload*8 - 1) // (self.packet_payload*8)
            packet_len = self.packet_payload*8 + self.packet_header*8
            data.size = num_packets * packet_len
            return self.latency
        
        def unpack_data(self, data):
            # Remove header from data payload
            num_packets = data.size // (self.packet_payload*8 + self.packet_header*8)
            data.size = num_packets * self.packet_payload*8
            return self.latency

    class ErrorCorrectionUnit():
        def __init__(self, args):
            self.type = args['type']
            
            # check if the field exists in args
            if self.type == 'CRC':
                if 'data_size' not in args:
                    print('Exit: Data size not specified for CRC')
                    sys.exit()
                if 'redundancy' not in args:
                    print('Exit: Redundancy not specified for CRC')
                    sys.exit()
            if self.type == 'Hamming':
                if 'data_size' not in args:
                    print('Exit: Data size not specified for Hamming')
                    sys.exit()
                if 'redundancy' not in args:
                    print('Exit: Redundancy not specified for Hamming')
                    sys.exit()

            self.datasize = args['data_size'] * 8 if 'data_size' in args else None # in bits
            self.redundancy = args['redundancy'] if 'redundancy' in args else None # in bits

            if args['latency'] is None:
                print('Exit: Latency not specified for error correction unit')
                sys.exit()
            if args['static_power'] is None:
                print('Exit: Static power not specified for error correction unit')
                sys.exit()
            if args['dynamic_power'] is None:
                print('Exit: Dynamic power not specified for error correction unit')
                sys.exit()
            self.latency = args['latency']
            self.static_power = args['static_power']
            self.dynamic_power = args['dynamic_power']
            
            if debug_ecc:
                print("==========================")
                print("Error correction unit")
                print("Type:", self.type)
                print("Data size:", self.datasize)
                print("Redundancy:", self.redundancy)
                print("==========================")

        
        def encode_data(self, data):
            if debug_ecc:
                print("ECC encode data", self.type)
            if self.type == 'Parity':
                self.datasize = data.size
                self.redundancy = int(np.sqrt(self.datasize)) * 2

            data.redundancy = self.redundancy
            num_blocks = (data.size + self.datasize - 1) // self.datasize
            if debug_ecc:
                print("Num blocks:", num_blocks)
            added_bits = num_blocks * self.redundancy
            data.size += added_bits

            return self.latency
        
        def decode_data(self, data):
            if debug_ecc:
                print("ECC decode data", self.type)
            if self.type == 'Parity':
                self.datasize = data.size
                self.redundancy = int(np.sqrt(self.datasize)) * 2
                
            data.redundancy = 0
            # data.errors -= min(0, self.correction)
            num_blocks = (data.size + self.datasize + self.redundancy - 1) // (self.datasize + self.redundancy)
            if debug_ecc:
                print("Num blocks:", num_blocks)
            added_bits = num_blocks * self.redundancy
            data.size = data.size - added_bits

            return self.latency

    def TXRX_match(self, dst_node_commlink):
        TRX_match = False
        for t in self.tx:
            for r in dst_node_commlink.rx:
                if t.check_TRX_match(r):
                    TRX_match = True
                    tx, rx = t, r
                    break
        if not TRX_match:
            print('Exit: Data transceiver not matched')
            sys.exit()
        return tx, rx
    
    def _get_pipelined_send_time(self, data):
        """Calculate send time based on pipelining configuration.
        
        Pipelining modes:
        - "sequential": Traditional - transmit only after all compute done
        - "per_block": Start transmitting each data block as soon as it's computed
        - "overlap": Allow partial overlap with configurable overlap_ratio
        
        Args:
            data: DataBlock with start_time and duration
            
        Returns:
            The adjusted send time based on pipelining settings
        """
        pipelining_config = self.node.pipelining.get('compute_comm', {})
        enabled = pipelining_config.get('enabled', False)
        mode = pipelining_config.get('mode', 'sequential')
        overlap_ratio = pipelining_config.get('overlap_ratio', 0.0)
        
        # Default: sequential (send after compute completes)
        if not enabled or mode == 'sequential':
            return data.start_time + data.duration
        
        if mode == 'per_block':
            # For per_block mode, still need to wait for this block's compute
            # but can overlap with other blocks
            return data.start_time + data.duration
        
        if mode == 'overlap':
            # overlap_ratio: fraction of compute time that overlaps with transmit
            # 0.0 = no overlap (sequential), 1.0 = full overlap (transmit starts at compute start)
            overlap_ratio = max(0.0, min(1.0, overlap_ratio))  # clamp to [0, 1]
            overlap_time = data.duration * overlap_ratio
            return data.start_time + data.duration - overlap_time
        
        # Fallback: sequential
        return data.start_time + data.duration
    
    def transmit_data(self, dst_node):
        data_blocks = self.node.data_blocks
        ecc = self.ecc is not None

        print("Transmitting data from", self.node.name, "to", dst_node.name)
        
        # Get pipelining configuration
        pipelining_config = self.node.pipelining.get('compute_comm', {})
        pipelining_enabled = pipelining_config.get('enabled', False)
        pipelining_mode = pipelining_config.get('mode', 'sequential')
        
        if pipelining_enabled:
            print(f"  [Pipelining] compute_comm enabled, mode: {pipelining_mode}")

        for idx, trial in enumerate(self.node.data_blocks):
            # Check if dst_node has received data from multiple src_nodes
            if dst_node.data_blocks != []:
                # Send after the last data block
                send_time = dst_node.data_blocks[idx][-1].start_time + dst_node.data_blocks[idx][-1].duration
            else:
                # Use pipelining-aware send time for first block
                send_time = self._get_pipelined_send_time(trial[0])
            for block_idx, data in enumerate(trial):
                # Calculate data ready time based on pipelining mode
                data_ready_time = self._get_pipelined_send_time(data)
                send_time = max(send_time, data_ready_time)

                latency = 0
                self.dynamic_power = 0
                if self.compression:
                    latency += self.compression.compress_data(data)
                    self.dynamic_power += self.compression.dynamic_power
                if self.protocol:
                    latency += self.protocol.pack_data(data)
                    self.dynamic_power += self.protocol.dynamic_power
                if self.ecc:
                    if debug_ecc:
                        print("Data size before ecc:", data.size)
                    latency += self.ecc.encode_data(data)
                    if debug_ecc:
                        print("Data size after ecc:", data.size)
                    self.dynamic_power += self.ecc.dynamic_power
                tx, rx = self.TXRX_match(dst_node.comm_link)
                latency += tx.transmit_data(data, send_time)
                self.dynamic_power += tx.dynamic_power

                print(f'Comm link (TX) for node {self.node.id}/ Discharge power: {self.dynamic_power} mW, Start time: {send_time}, End time: {send_time + latency}')
                self.node.pmu.discharge_power(self.dynamic_power, send_time, latency, 'comm_link')
                UPDATE_STATS(self.node, send_time, latency, 'transmit data', data_trx=tx)
                send_time += latency
        
        # Print data blocks
        # for idx, trial in enumerate(data_blocks):
        #     print(f'Trial {idx+1}:')
        #     for data in trial:
        #         print(f'Start time: {data.start_time}, Duration: {data.duration}, Size: {data.size}')
        return data_blocks

    def _get_pipelined_receive_end_time(self, receive_start_time, receive_duration):
        """Calculate when next stage (compute) can start based on comm_compute pipelining.
        
        Pipelining modes:
        - "sequential": Traditional - compute starts only after all data received
        - "overlap": Allow partial overlap - compute can start before receive completes
        
        Args:
            receive_start_time: When data reception starts
            receive_duration: How long data reception takes
            
        Returns:
            The adjusted start time for the next stage (compute) based on pipelining settings
        """
        pipelining_config = self.node.pipelining.get('comm_compute', {})
        enabled = pipelining_config.get('enabled', False)
        mode = pipelining_config.get('mode', 'sequential')
        overlap_ratio = pipelining_config.get('overlap_ratio', 0.0)
        
        full_end_time = receive_start_time + receive_duration
        
        # Default: sequential (compute starts after receive completes)
        if not enabled or mode == 'sequential':
            return full_end_time
        
        if mode == 'overlap':
            # overlap_ratio: fraction of receive time that overlaps with compute
            # 0.0 = no overlap (sequential), 1.0 = full overlap (compute starts at receive start)
            overlap_ratio = max(0.0, min(1.0, overlap_ratio))  # clamp to [0, 1]
            overlap_time = receive_duration * overlap_ratio
            return full_end_time - overlap_time
        
        # Fallback: sequential
        return full_end_time

    def receive_data(self, src_node, prop_latency):
        data_blocks = self.node.data_blocks
        ecc = self.ecc is not None

        print("Receive data from", src_node.name, "to", self.node.name)
        
        # Get pipelining configuration
        pipelining_config = self.node.pipelining.get('comm_compute', {})
        pipelining_enabled = pipelining_config.get('enabled', False)
        pipelining_mode = pipelining_config.get('mode', 'sequential')
        
        if pipelining_enabled:
            print(f"  [Pipelining] comm_compute enabled, mode: {pipelining_mode}")
        
        number_of_retransmissions = 0

        for idx, trial in enumerate(self.node.data_blocks):
            for block_idx, data in enumerate(trial):
                data_ready_time = data.start_time
                receive_time = data_ready_time + prop_latency
                data.start_time = receive_time

                if debug_ecc:
                    print("Data size at ecc receive:", data.size)

                latency = 0
                self.dynamic_power = 0
                tx, rx = src_node.comm_link.TXRX_match(self)
                rx_latency = rx.receive_data(data)
                latency += rx_latency
                self.dynamic_power += rx.dynamic_power
                
                BER = get_BER(data, rx)
                # sys.__stdout__.write(f'BER: {BER}\n')

                uncorrected_errors = int(BER * data.size)  # bits in error
                corrected_errors = 0
                retransmission_delay = 0
                total_bits = data.size

                if ecc:
                    method = self.ecc.type
                    latency += self.ecc.decode_data(data)
                    self.dynamic_power += self.ecc.dynamic_power
                    required_BER = 1e-5

                    if method == 'CRC':
                        block_size_bits = self.ecc.datasize
                        num_blocks = (total_bits + block_size_bits - 1) // block_size_bits
                        prob_error_per_block = 1 - (1 - BER) ** block_size_bits
                        print(block_size_bits, num_blocks, prob_error_per_block)

                        # Target block failure probability
                        target_block_failure_prob = (required_BER * total_bits) / (block_size_bits * num_blocks)

                        if prob_error_per_block > 0 and prob_error_per_block < 1 and target_block_failure_prob < 1:
                            N = math.ceil(math.log(target_block_failure_prob) / math.log(prob_error_per_block))
                        elif math.isclose(prob_error_per_block, 1):
                            sys.exit('Exit: Prob error per block is 1, cannot calculate N')
                        else:
                            N = 0

                        if debug_ecc:
                            print("Retransmit N:", N)

                        retransmission_delay += N * num_blocks * self.ecc.datasize / rx.data_rate * 1e-3  # ms
                        corrected_errors = min(uncorrected_errors, N * num_blocks * block_size_bits)
                        BER_corrected = max((uncorrected_errors - corrected_errors) / total_bits, 0)

                    elif method == 'Parity':
                        r = self.ecc.redundancy
                        max_correctable = r
                        residual_errors = max(0, uncorrected_errors - max_correctable)
                        residual_BER = residual_errors / total_bits if total_bits > 0 else 1.0

                        if residual_BER > required_BER:
                            N = math.ceil(math.log(required_BER) / math.log(residual_BER))
                        else:
                            N = 0

                        if debug_ecc:
                            print("Retransmit N:", N)

                        corrected_errors = min(uncorrected_errors, N * max_correctable)
                        retransmission_delay += N * total_bits / rx.data_rate * 1e-3
                        BER_corrected = max((uncorrected_errors - corrected_errors) / total_bits, 0)

                    elif method == 'Hamming':
                        r = self.ecc.redundancy
                        if r == 0:
                            BER_corrected = BER  # No correction capability
                        else:
                            hamming_block_size = 2**r - r - 1
                            correctable_bits = total_bits // hamming_block_size
                            residual_errors = max(0, uncorrected_errors - correctable_bits)
                            residual_BER = residual_errors / total_bits if total_bits > 0 else 1.0

                            if residual_BER > required_BER:
                                N = math.ceil(math.log(required_BER) / math.log(residual_BER))
                            else:
                                N = 0

                            if debug_ecc:
                                print("Retransmit N:", N)

                            corrected_errors = min(uncorrected_errors, N * correctable_bits)
                            retransmission_delay += N * total_bits / rx.data_rate * 1e-3
                            BER_corrected = max((uncorrected_errors - corrected_errors) / total_bits, 0)

                    # N is the number of retransmissions needed to achieve the required BER
                    number_of_retransmissions += N
                
                # if ecc:
                    # latency += self.ecc.decode_data(data)
                    # self.dynamic_power += self.ecc.dynamic_power
                if self.protocol:
                    latency += self.protocol.unpack_data(data)
                    self.dynamic_power += self.protocol.dynamic_power
                if self.compression:
                    latency += self.compression.decompress_data(data)
                    self.dynamic_power += self.compression.dynamic_power
                

                latency += retransmission_delay
                self.dynamic_power += rx. dynamic_power * (retransmission_delay / rx_latency)

                print(f'Retransmission delay: {retransmission_delay} ms')
                print(f'Comm link (RX) for node {self.node.id}/ Discharge power: {self.dynamic_power} mW, Start time: {receive_time}, End time: {receive_time + latency}')
                self.node.pmu.discharge_power(self.dynamic_power, receive_time, latency, 'comm_link')
                UPDATE_STATS(self.node, receive_time, latency, 'receive data', data_trx=rx)
                
                # Apply comm_compute pipelining: adjust when next stage can start
                # data.start_time is used by the next compute stage
                pipelined_end_time = self._get_pipelined_receive_end_time(receive_time, latency)
                data.start_time = pipelined_end_time
                data.duration = latency  # Store actual receive duration for reference
        
        # Print data blocks
        # for idx, trial in enumerate(data_blocks):
        #     print(f'Trial {idx+1}:')
        #     for data in trial:
        #         print(f'Start time: {data.start_time}, Duration: {data.duration}, Size: {data.size}')
        return data_blocks, BER, number_of_retransmissions