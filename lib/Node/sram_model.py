"""
CACTI-based SRAM Power and Area Model

This module provides SRAM sizing and power estimation with two modes:
1. LUT mode: Uses precomputed lookup table (fast)
2. Direct CACTI mode: Calls CACTI binary for accurate results

CACTI Reference: https://github.com/HewlettPackard/cacti
"""

import math
import os
import subprocess
import tempfile
import re


class CACTIRunner:
    """Wrapper to run CACTI binary and parse results."""
    
    def __init__(self, cacti_path=None):
        """
        Initialize CACTI runner.
        
        Args:
            cacti_path: Path to CACTI binary. If None, searches common locations.
        """
        self.cacti_path = cacti_path or self._find_cacti()
        
    def _find_cacti(self):
        """Find CACTI binary in common locations."""
        search_paths = [
            "/home/daniel137/neuroteam/cacti_src/cacti",
            os.path.expanduser("~/cacti/cacti"),
            os.path.expanduser("~/cacti_src/cacti"),
            "/usr/local/bin/cacti",
            "/usr/bin/cacti",
        ]
        
        for path in search_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        
        return None
    
    def is_available(self):
        """Check if CACTI is available."""
        return self.cacti_path is not None
    
    def generate_config(self, size_bytes, technology_um, block_size=64, 
                        cell_type="itrs-lstp", temperature_k=300):
        """
        Generate CACTI configuration file content for SRAM.
        
        Args:
            size_bytes: SRAM size in bytes
            technology_um: Technology node in micrometers (e.g., 0.045 for 45nm)
            block_size: Block size in bytes
            cell_type: Cell type (itrs-hp, itrs-lstp, itrs-lop)
            temperature_k: Operating temperature in Kelvin
        
        Returns:
            Configuration file content as string
        """
        config = f"""# CACTI Configuration for SRAM (auto-generated)

# Cache size
-size (bytes) {size_bytes}

# Block size (bytes)
-block size (bytes) {block_size}

# Set associativity (1 for direct-mapped SRAM buffer)
-associativity 1

# Ports
-read-write port 1
-exclusive read port 0
-exclusive write port 0
-single ended read ports 0

# Banks
-UCA bank count 1

# Technology node
-technology (u) {technology_um}

# Power gating (disabled for accurate baseline)
-Array Power Gating - "false"
-WL Power Gating - "false"
-CL Power Gating - "false"
-Bitline floating - "false"
-Interconnect Power Gating - "false"
-Power Gating Performance Loss 0.01

# Memory parameters
-page size (bits) 8192 
-burst length 8
-internal prefetch width 8

# Cell type - use low standby power for BCI implant
-Data array cell type - "{cell_type}"
-Data array peripheral type - "{cell_type}"
-Tag array cell type - "{cell_type}"
-Tag array peripheral type - "{cell_type}"

# Bus width
-output/input bus width 64

# Operating temperature
-operating temperature (K) {temperature_k}

# Model as RAM (no tag array) for SRAM buffer
-cache type "ram"

# Tag size (not used for RAM)
-tag size (b) "default"

# Access mode
-access mode (normal, sequential, fast) - "fast"

# Design objective: minimize power
-design objective (weight delay, dynamic power, leakage power, cycle time, area) 0 20 80 0 0
-deviate (delay, dynamic power, leakage power, cycle time, area) 100000 100000 100000 100000 100000

# Optimization target: leakage power
-Optimize ED or ED^2 (ED, ED^2, NONE): "ED"

# Print level (1 = minimal)
-Print level (DETAILED, CONCISE) - "CONCISE"

# ECC
-Add ECC - "false"
"""
        return config
    
    def run(self, size_bytes, technology_nm, cell_type="itrs-lstp", temperature_k=300):
        """
        Run CACTI for given SRAM configuration.
        
        Args:
            size_bytes: SRAM size in bytes
            technology_nm: Technology node in nm (e.g., 45)
            cell_type: Cell type
            temperature_k: Temperature in Kelvin
        
        Returns:
            dict with parsed results or None if failed
        """
        if not self.is_available():
            return None
        
        # Convert nm to um for CACTI
        technology_um = technology_nm / 1000.0
        
        # Ensure minimum size (CACTI has minimums)
        size_bytes = max(size_bytes, 512)
        
        # Calculate appropriate block size
        block_size = min(64, size_bytes)
        
        # Generate config
        config_content = self.generate_config(
            size_bytes=size_bytes,
            technology_um=technology_um,
            block_size=block_size,
            cell_type=cell_type,
            temperature_k=temperature_k
        )
        
        # Write to temp file and run CACTI
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg', delete=False) as f:
                f.write(config_content)
                config_path = f.name
            
            # Run CACTI
            result = subprocess.run(
                [self.cacti_path, '-infile', config_path],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.path.dirname(self.cacti_path)
            )
            
            # Clean up
            os.unlink(config_path)
            
            if result.returncode != 0:
                return None
            
            # Parse output
            return self._parse_output(result.stdout, size_bytes)
            
        except subprocess.TimeoutExpired:
            return None
        except Exception as e:
            return None
    
    def _parse_output(self, output, size_bytes):
        """Parse CACTI output to extract power and area metrics."""
        result = {
            'size_bytes': size_bytes,
            'size_kb': size_bytes / 1024,
            'access_time_ns': 0,
            'cycle_time_ns': 0,
            'dynamic_read_energy_nj': 0,
            'dynamic_write_energy_nj': 0,
            'leakage_power_mw': 0,
            'gate_leakage_power_mw': 0,
            'area_mm2': 0,
            'height_mm': 0,
            'width_mm': 0,
        }
        
        # Parse patterns
        patterns = {
            'access_time_ns': r'Access time \(ns\):\s*([\d.]+)',
            'cycle_time_ns': r'Cycle time \(ns\):\s*([\d.]+)',
            'dynamic_read_energy_nj': r'Total dynamic read energy per access \(nJ\):\s*([\d.]+)',
            'dynamic_write_energy_nj': r'Total dynamic write energy per access \(nJ\):\s*([\d.]+)',
            'leakage_power_mw': r'Total leakage power of a bank.*?:\s*([\d.]+)',
            'gate_leakage_power_mw': r'Total gate leakage power of a bank.*?:\s*([\d.]+)',
        }
        
        # Area pattern (height x width)
        area_pattern = r'height x width \(mm\):\s*([\d.]+)\s*x\s*([\d.]+)'
        
        for key, pattern in patterns.items():
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                result[key] = float(match.group(1))
        
        # Parse area
        area_match = re.search(area_pattern, output, re.IGNORECASE)
        if area_match:
            result['height_mm'] = float(area_match.group(1))
            result['width_mm'] = float(area_match.group(2))
            result['area_mm2'] = result['height_mm'] * result['width_mm']
        
        # Calculate derived metrics
        result['area_um2'] = result['area_mm2'] * 1e6  # mm² to μm²
        result['static_power_per_kb_uw'] = (result['leakage_power_mw'] * 1000) / result['size_kb'] if result['size_kb'] > 0 else 0
        result['dynamic_read_energy_pj'] = result['dynamic_read_energy_nj'] * 1000  # nJ to pJ
        result['dynamic_write_energy_pj'] = result['dynamic_write_energy_nj'] * 1000
        
        return result


class SRAMModel:
    """
    SRAM model with optional CACTI binary integration.
    
    Supports two modes:
    1. LUT mode: Uses precomputed lookup table (fast)
    2. Direct mode: Calls CACTI binary for each query (accurate)
    """
    
    # Default CACTI lookup table (from CACTI 7.0 simulations)
    # Using LSTP (Low Standby Power) cells - suitable for implants
    # Configuration: direct-mapped (associativity=1) scratchpad, LSTP cells
    # Format: technology_node -> [static_power_per_KB (μW), read_energy (pJ), write_energy (pJ), area_per_KB (μm²)]
    # Static power scales with size, dynamic energy is per 64-byte block access
    DEFAULT_CACTI_LUT = {
        "90": [0.221, 388.62, 766.10, 17374],   # 90nm LSTP direct-mapped scratchpad
        "65": [0.387, 222.59, 432.90, 9096],    # 65nm LSTP direct-mapped scratchpad
        "45": [0.278, 122.62, 234.01, 4358],    # 45nm LSTP direct-mapped scratchpad
        "32": [0.332, 70.25, 129.70, 2205],     # 32nm LSTP direct-mapped scratchpad
        "22": [0.270, 38.31, 49.33, 1790],      # 22nm LSTP direct-mapped scratchpad
    }
    
    def __init__(self, config: dict):
        """
        Initialize SRAM model with configuration.
        
        Args:
            config: SRAM model configuration dict from TierX.yaml
                - enabled: bool
                - use_cacti_binary: bool (default: True if available)
                - cacti_path: str (optional)
                - technology_node: int (nm)
                - cell_type: str (itrs-hp, itrs-lstp, itrs-lop)
                - buffer_depth_ms: float (milliseconds)
                - include_intermediate: bool
                - intermediate_multiplier: float
                - cacti_lut: dict (optional, override default LUT)
                - sampling_rate: int (Hz)
                - read_write_ratio: float
                - temperature_k: int (Kelvin)
        """
        self.enabled = config.get('enabled', False)
        self.technology_node = config.get('technology_node', 45)
        self.technology_node_str = str(self.technology_node)
        self.cell_type = config.get('cell_type', 'itrs-lstp')
        self.buffer_depth_ms = config.get('buffer_depth_ms', 10)
        self.include_intermediate = config.get('include_intermediate', True)
        self.intermediate_multiplier = config.get('intermediate_multiplier', 2.0)
        self.use_kernel_based_intermediate = config.get('use_kernel_based_intermediate', True)
        self.sampling_rate = config.get('sampling_rate', 30000)
        self.read_write_ratio = config.get('read_write_ratio', 0.5)
        self.temperature_k = config.get('temperature_k', 310)  # Body temperature
        
        # Kernel info for intermediate buffer calculation (set via set_kernel_info)
        self.kernel_dimensions = None
        self.num_kernels = 0
        self.kernel_sram_accesses = None  # Per-kernel SRAM access counts [(reads, writes), ...]
        self.esp_plm_overrides = None    # ESP PLM buffer overrides (set via set_esp_plm_overrides)
        
        # Default per-kernel SRAM access pattern
        default_accesses = config.get('default_kernel_accesses', {})
        self.default_kernel_reads = default_accesses.get('reads', 1)
        self.default_kernel_writes = default_accesses.get('writes', 1)
        
        # Initialize CACTI runner
        cacti_path = config.get('cacti_path', None)
        self.cacti_runner = CACTIRunner(cacti_path)
        
        # Determine mode
        use_cacti_binary = config.get('use_cacti_binary', True)
        self.use_direct_cacti = use_cacti_binary and self.cacti_runner.is_available()
        
        # Load CACTI LUT (used as fallback or in LUT mode)
        self.cacti_lut = config.get('cacti_lut', self.DEFAULT_CACTI_LUT)
        
        # Get LUT coefficients for selected technology
        if self.technology_node_str in self.cacti_lut:
            coeffs = self.cacti_lut[self.technology_node_str]
        else:
            coeffs = self.cacti_lut.get("45", [0.278, 122.62, 234.01, 4358])
        
        self.static_power_per_kb = coeffs[0]      # μW per KB
        self.read_energy_per_access = coeffs[1]   # pJ per read access
        self.write_energy_per_access = coeffs[2]  # pJ per write access
        self.area_per_kb = coeffs[3]              # μm² per KB
        
        # Cache for CACTI results
        self._cacti_cache = {}
        
        # Calculated values
        self.sram_size_bytes = 0
        self.sram_size_kb = 0
        self.input_buffer_size = 0
        self.intermediate_buffer_size = 0
        self.kernel_buffer_breakdown = []  # Per-kernel buffer sizes
        self.static_power_mw = 0
        self.dynamic_power_mw = 0
        self.total_power_mw = 0
        self.area_um2 = 0
    
    def set_kernel_info(self, num_kernels: int, kernel_dimensions: list, kernel_sram_accesses: list = None):
        """
        Set kernel pipeline information for intermediate buffer calculation.
        
        Args:
            num_kernels: Number of kernels in the pipeline
            kernel_dimensions: List of (spatial, temporal) tuples for each kernel output
                               Can also be a flat list where each element is the output dimension
            kernel_sram_accesses: List of (reads, writes) tuples per kernel, or None for defaults
                                  Each tuple specifies SRAM read/write counts per kernel execution
        """
        self.num_kernels = num_kernels
        self.kernel_dimensions = kernel_dimensions
        
        # Set per-kernel SRAM access counts
        if kernel_sram_accesses is not None and len(kernel_sram_accesses) > 0:
            self.kernel_sram_accesses = kernel_sram_accesses
        else:
            # Use default access counts for all kernels
            self.kernel_sram_accesses = [(self.default_kernel_reads, self.default_kernel_writes)] * num_kernels

    def set_esp_plm_overrides(self, overrides: list):
        """Set ESP PLM buffer size overrides for ESP-enabled PEs.

        When an accelerator is synthesized through ESP, the PLM (Private Local
        Memory) sizes are known exactly from the ``chunk_factor`` and
        ``data_bitwidth`` fields.  These override the generic SRAM sizing
        for the corresponding kernel indices.

        Args:
            overrides: List of dicts with keys:
                - kernel_idx: int
                - plm_input_bytes: int
                - plm_output_bytes: int
                - in_place: bool
        """
        self.esp_plm_overrides = overrides
    
    def _calculate_kernel_based_intermediate_buffer(self, bit_precision: int = 16, 
                                                     electrodes: int = 100,
                                                     double_buffer: bool = True) -> int:
        """
        Calculate intermediate buffer size based on kernel pipeline structure.
        
        For a pipeline with N kernels, we need buffers between stages.
        Each buffer size is determined by the kernel's output dimension.
        With double buffering, each stage needs 2x the buffer size for ping-pong.
        
        Args:
            bit_precision: Bits per value (default: 16)
            electrodes: Number of electrodes (for 'num_elec' placeholder)
            double_buffer: Use double buffering for pipelining (default: True)
        
        Returns:
            Total intermediate buffer size in bytes
        """
        if self.kernel_dimensions is None or self.num_kernels == 0:
            return 0
        
        bytes_per_value = math.ceil(bit_precision / 8)
        buffer_multiplier = 2 if double_buffer else 1
        
        total_buffer = 0
        self.kernel_buffer_breakdown = []
        
        for i, dim in enumerate(self.kernel_dimensions):
            if isinstance(dim, (list, tuple)):
                # (spatial, temporal) format
                spatial = dim[0]
                temporal = dim[1] if len(dim) > 1 else 1
                
                # Handle 'num_elec' or similar expressions
                if isinstance(spatial, str):
                    try:
                        spatial = eval(spatial.replace('num_elec', str(electrodes)))
                    except:
                        spatial = electrodes
                
                output_size = spatial * temporal
            else:
                # Single dimension value
                output_size = dim if isinstance(dim, int) else electrodes
            
            buffer_size = output_size * bytes_per_value * buffer_multiplier

            # ESP PLM override: use known PLM sizes from ESP synthesis
            if self.esp_plm_overrides:
                for ov in self.esp_plm_overrides:
                    if ov['kernel_idx'] == i:
                        plm_total = ov['plm_input_bytes'] + ov['plm_output_bytes']
                        buffer_size = max(buffer_size, plm_total)
                        break

            total_buffer += buffer_size
            
            # Get per-kernel SRAM access counts
            if self.kernel_sram_accesses and i < len(self.kernel_sram_accesses):
                kernel_reads, kernel_writes = self.kernel_sram_accesses[i]
            else:
                kernel_reads, kernel_writes = self.default_kernel_reads, self.default_kernel_writes
            
            self.kernel_buffer_breakdown.append({
                'kernel_idx': i,
                'output_dimension': output_size,
                'buffer_bytes': buffer_size,
                'sram_reads': kernel_reads,
                'sram_writes': kernel_writes
            })
        
        return total_buffer
    
    def calculate_sram_requirements(self, electrodes: int, bit_precision: int = 16, 
                                     sampling_rate: int = None) -> dict:
        """
        Calculate SRAM size and power based on electrode count.
        
        Uses direct CACTI call if available, otherwise falls back to LUT.
        
        Args:
            electrodes: Number of electrodes
            bit_precision: Bits per sample (default: 16)
            sampling_rate: Samples per second per electrode (uses config if None)
        
        Returns:
            dict with SRAM specifications
        """
        if not self.enabled:
            return self._empty_result()
        
        if sampling_rate is None:
            sampling_rate = self.sampling_rate
        
        # Calculate input buffer size
        samples_in_buffer = int(sampling_rate * (self.buffer_depth_ms / 1000.0))
        bytes_per_sample = math.ceil(bit_precision / 8)
        
        self.input_buffer_size = electrodes * samples_in_buffer * bytes_per_sample
        
        # Calculate intermediate buffer size
        # If num_kernels == 0, no processing happens on this tier, so no intermediate buffer needed
        if self.include_intermediate and self.num_kernels > 0:
            if self.use_kernel_based_intermediate and self.kernel_dimensions is not None and len(self.kernel_dimensions) > 0:
                # Kernel-aware intermediate buffer calculation
                self.intermediate_buffer_size = self._calculate_kernel_based_intermediate_buffer(
                    bit_precision=bit_precision,
                    electrodes=electrodes,
                    double_buffer=True
                )
            else:
                # Fallback to multiplier-based calculation (kernels exist but dimensions not specified)
                self.intermediate_buffer_size = int(self.input_buffer_size * self.intermediate_multiplier)
        else:
            # No processing on this tier, no intermediate buffer needed
            self.intermediate_buffer_size = 0
        
        self.sram_size_bytes = self.input_buffer_size + self.intermediate_buffer_size
        self.sram_size_kb = self.sram_size_bytes / 1024.0
        
        # Try direct CACTI call
        cacti_direct = False
        if self.use_direct_cacti:
            cacti_result = self._get_cacti_params(self.sram_size_bytes)
            if cacti_result is not None:
                cacti_direct = True
                
                # Use CACTI results
                self.static_power_mw = cacti_result['leakage_power_mw']
                self.area_um2 = cacti_result['area_um2']
                
                # Dynamic power calculation with separate read/write energy
                # CACTI energy is per 64-byte block access
                bytes_per_sample = math.ceil(bit_precision / 8)
                block_size = 64  # CACTI block size
                samples_per_block = block_size // bytes_per_sample
                
                # Calculate total reads and writes considering per-kernel access patterns
                total_read_accesses = 0
                total_write_accesses = 0
                
                if self.kernel_buffer_breakdown:
                    # Use per-kernel SRAM access counts
                    for kernel_info in self.kernel_buffer_breakdown:
                        kernel_reads = kernel_info.get('sram_reads', self.default_kernel_reads)
                        kernel_writes = kernel_info.get('sram_writes', self.default_kernel_writes)
                        
                        kernel_accesses_per_sec = (electrodes * sampling_rate) / samples_per_block
                        total_read_accesses += kernel_accesses_per_sec * kernel_reads
                        total_write_accesses += kernel_accesses_per_sec * kernel_writes
                else:
                    # Fallback: simple 1 read + 1 write per sample access pattern
                    sample_accesses_per_sec = sampling_rate * electrodes
                    block_accesses_per_sec = sample_accesses_per_sec / samples_per_block
                    total_read_accesses = block_accesses_per_sec * self.default_kernel_reads
                    total_write_accesses = block_accesses_per_sec * self.default_kernel_writes
                
                # Dynamic power with separate read/write energy
                read_power_mw = (total_read_accesses * cacti_result['dynamic_read_energy_pj']) / 1e9
                write_power_mw = (total_write_accesses * cacti_result['dynamic_write_energy_pj']) / 1e9
                self.dynamic_power_mw = read_power_mw + write_power_mw
                
                self.total_power_mw = self.static_power_mw + self.dynamic_power_mw
                
                return {
                    'input_buffer_bytes': self.input_buffer_size,
                    'intermediate_buffer_bytes': self.intermediate_buffer_size,
                    'kernel_buffer_breakdown': self.kernel_buffer_breakdown if self.kernel_buffer_breakdown else None,
                    'total_sram_bytes': self.sram_size_bytes,
                    'total_sram_kb': self.sram_size_kb,
                    'static_power_mw': self.static_power_mw,
                    'dynamic_power_mw': self.dynamic_power_mw,
                    'total_power_mw': self.total_power_mw,
                    'area_um2': self.area_um2,
                    'technology_node': self.technology_node,
                    'kernel_based_intermediate': self.use_kernel_based_intermediate and self.kernel_dimensions is not None,
                    'cacti_direct': True,
                    'cacti_result': cacti_result,
                }
        
        # LUT-based calculation (fallback)
        self.static_power_mw = (self.sram_size_kb * self.static_power_per_kb) / 1000.0
        
        # Dynamic power: CACTI energy is per 64-byte block access
        # Calculate block accesses per second based on per-kernel SRAM access patterns
        bytes_per_sample = math.ceil(bit_precision / 8)
        block_size = 64  # CACTI block size
        samples_per_block = block_size // bytes_per_sample
        
        # Calculate total reads and writes considering per-kernel access patterns
        total_read_accesses = 0
        total_write_accesses = 0
        
        if self.kernel_buffer_breakdown:
            # Use per-kernel SRAM access counts
            for kernel_info in self.kernel_buffer_breakdown:
                kernel_reads = kernel_info.get('sram_reads', self.default_kernel_reads)
                kernel_writes = kernel_info.get('sram_writes', self.default_kernel_writes)
                output_dim = kernel_info['output_dimension']
                
                # Accesses per second for this kernel (output_dim activations per inference)
                kernel_accesses_per_sec = (electrodes * sampling_rate) / samples_per_block
                total_read_accesses += kernel_accesses_per_sec * kernel_reads
                total_write_accesses += kernel_accesses_per_sec * kernel_writes
        else:
            # Fallback: simple 1 read + 1 write per sample access pattern
            sample_accesses_per_sec = electrodes * sampling_rate
            block_accesses_per_sec = sample_accesses_per_sec / samples_per_block
            total_read_accesses = block_accesses_per_sec * self.default_kernel_reads
            total_write_accesses = block_accesses_per_sec * self.default_kernel_writes
        
        # Dynamic power with separate read/write energy
        read_power_mw = (total_read_accesses * self.read_energy_per_access) / 1e9
        write_power_mw = (total_write_accesses * self.write_energy_per_access) / 1e9
        self.dynamic_power_mw = read_power_mw + write_power_mw
        
        self.total_power_mw = self.static_power_mw + self.dynamic_power_mw
        self.area_um2 = self.sram_size_kb * self.area_per_kb
        
        return {
            'input_buffer_bytes': self.input_buffer_size,
            'intermediate_buffer_bytes': self.intermediate_buffer_size,
            'kernel_buffer_breakdown': self.kernel_buffer_breakdown if self.kernel_buffer_breakdown else None,
            'total_sram_bytes': self.sram_size_bytes,
            'total_sram_kb': self.sram_size_kb,
            'static_power_mw': self.static_power_mw,
            'dynamic_power_mw': self.dynamic_power_mw,
            'total_power_mw': self.total_power_mw,
            'area_um2': self.area_um2,
            'technology_node': self.technology_node,
            'kernel_based_intermediate': self.use_kernel_based_intermediate and self.kernel_dimensions is not None,
            'cacti_direct': False,
        }
    
    def _get_cacti_params(self, size_bytes):
        """Get parameters from direct CACTI call with caching."""
        cache_key = (size_bytes, self.technology_node, self.cell_type, self.temperature_k)
        if cache_key in self._cacti_cache:
            return self._cacti_cache[cache_key]
        
        result = self.cacti_runner.run(
            size_bytes=size_bytes,
            technology_nm=self.technology_node,
            cell_type=self.cell_type,
            temperature_k=self.temperature_k
        )
        
        if result is not None:
            self._cacti_cache[cache_key] = result
        
        return result
    
    def _empty_result(self) -> dict:
        """Return empty result when SRAM model is disabled."""
        return {
            'input_buffer_bytes': 0,
            'intermediate_buffer_bytes': 0,
            'total_sram_bytes': 0,
            'total_sram_kb': 0,
            'static_power_mw': 0,
            'dynamic_power_mw': 0,
            'total_power_mw': 0,
            'area_um2': 0,
            'technology_node': None,
            'cacti_direct': False,
        }
    
    def get_power_breakdown(self) -> dict:
        """Get power breakdown for reporting."""
        return {
            'sram_static_power_mw': self.static_power_mw,
            'sram_dynamic_power_mw': self.dynamic_power_mw,
            'sram_total_power_mw': self.total_power_mw,
        }
    
    def generate_lut_from_cacti(self, sizes_kb=None):
        """
        Generate a lookup table by running CACTI for various sizes.
        
        Args:
            sizes_kb: List of SRAM sizes in KB to simulate
        
        Returns:
            dict with averaged metrics per KB
        """
        if not self.cacti_runner.is_available():
            print("CACTI binary not available")
            return None
        
        if sizes_kb is None:
            sizes_kb = [1, 4, 16, 64, 256, 1024]
        
        results = []
        print(f"\nGenerating LUT from CACTI ({self.technology_node}nm, {self.cell_type}):")
        for size_kb in sizes_kb:
            size_bytes = int(size_kb * 1024)
            result = self.cacti_runner.run(
                size_bytes=size_bytes,
                technology_nm=self.technology_node,
                cell_type=self.cell_type,
                temperature_k=self.temperature_k
            )
            if result:
                results.append(result)
                print(f"  {size_kb:4d} KB: leakage={result['leakage_power_mw']:8.4f} mW, "
                      f"read_energy={result['dynamic_read_energy_pj']:6.2f} pJ, "
                      f"area={result['area_mm2']*1e6:10.0f} μm²")
        
        if not results:
            return None
        
        # Calculate average per-KB metrics
        avg_static_per_kb = sum(r['leakage_power_mw'] / r['size_kb'] for r in results) / len(results)
        avg_dynamic_per_access = sum((r['dynamic_read_energy_pj'] + r['dynamic_write_energy_pj']) / 2 for r in results) / len(results)
        avg_area_per_kb = sum(r['area_um2'] / r['size_kb'] for r in results) / len(results)
        
        lut_entry = [
            avg_static_per_kb * 1000,  # mW to μW
            avg_dynamic_per_access,
            avg_area_per_kb,
        ]
        
        print(f"\nLUT entry for {self.technology_node}nm:")
        print(f"  \"{self.technology_node}\": [{lut_entry[0]:.2f}, {lut_entry[1]:.3f}, {lut_entry[2]:.0f}]")
        
        return lut_entry
    
    def __repr__(self):
        if not self.enabled:
            return "SRAMModel(disabled)"
        mode = "CACTI" if self.use_direct_cacti else "LUT"
        return (f"SRAMModel(tech={self.technology_node}nm, mode={mode}, "
                f"size={self.sram_size_kb:.2f}KB, power={self.total_power_mw:.3f}mW)")


# Convenience function
def calculate_sram_power(electrodes: int, config: dict, 
                         bit_precision: int = 16, 
                         sampling_rate: int = None) -> dict:
    """Calculate SRAM power for given electrode count."""
    model = SRAMModel(config)
    return model.calculate_sram_requirements(electrodes, bit_precision, sampling_rate)


# Test code
if __name__ == "__main__":
    test_config = {
        'enabled': True,
        'use_cacti_binary': True,
        'technology_node': 45,
        'cell_type': 'itrs-lstp',
        'buffer_depth_ms': 10,
        'include_intermediate': True,
        'intermediate_multiplier': 2.0,
        'sampling_rate': 30000,
        'read_write_ratio': 0.5,
        'temperature_k': 310,
    }
    
    model = SRAMModel(test_config)
    
    print("\n" + "=" * 70)
    print(f"SRAM Model Test ({model.technology_node}nm)")
    print(f"Mode: {'Direct CACTI' if model.use_direct_cacti else 'Lookup Table'}")
    if model.use_direct_cacti:
        print(f"CACTI path: {model.cacti_runner.cacti_path}")
    print("=" * 70)
    
    for electrodes in [100, 256, 512, 1000, 2000]:
        result = model.calculate_sram_requirements(electrodes, bit_precision=16)
        print(f"\nElectrodes: {electrodes}")
        print(f"  Total SRAM: {result['total_sram_kb']:.2f} KB")
        print(f"  Static power: {result['static_power_mw']:.4f} mW")
        print(f"  Dynamic power: {result['dynamic_power_mw']:.4f} mW")
        print(f"  Total power: {result['total_power_mw']:.4f} mW")
        print(f"  Area: {result['area_um2']:.0f} μm² ({result['area_um2']/1e6:.4f} mm²)")
        print(f"  CACTI Direct: {result.get('cacti_direct', False)}")
    
    # Generate LUT
    if model.cacti_runner.is_available():
        print("\n" + "=" * 70)
        model.generate_lut_from_cacti([4, 16, 64, 256, 1024])
