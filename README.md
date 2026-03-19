# TierX: A Simulation Framework for Multi-tier BCI System Design Evaluation and Exploration

## Overview

TierX is a simulation framework for exploring the design space of multi-tier BCI systems, enabling systematic evaluation of trade-offs across computation, communication, powering, and node placement choices.


## How to Run

### Prerequisites
- Python 3.10 or higher
- Required Python packages
   ```bash
   ./install.sh
   ```

### Quick Start
- Execute the simulation with default parameters:
   ```bash
   ./run.sh
   ```

### Command-Line Options
```
Usage: ./run.sh [options]

Config files:
  --tierx <path>          High-level config: apps, metrics, sweeps (default: TierX.yaml)
  --searchspace <path>    Low-level DSE params: electrodes, GA settings (default: SearchSpace.yaml)

Search strategy:
  --search <strategy>     Search strategy: exhaustive, pruning, ga (default: exhaustive)

Options:
  -v, --verbose           Verbose output
  -g, --graph-only        Only generate plots from existing data
  -p                      Enable power breakdown
  -l                      Enable latency breakdown
  --fast                  Use pruning search (faster)
  --lightweight           Disable graphs/runspace/post-analysis
```

### Case Study Scripts
Additional helper shell scripts for case-study style experiments are grouped under `scripts_case_study/`.
They are optional and not required for the standard workflow.

### Customization
Modify `TierX.yaml` (high-level sweep parameters) and `SearchSpace.yaml` (low-level DSE parameters) — details in the tables below. Per-application workload configs are in `lib/Input/{ApplicationName}/`, and processing pipelines are defined in `lib/Input/PEs.yaml`. See [Adding a New Application](#adding-a-new-application) for custom pipelines.

## Configuration Files

### High-Level Parameters (`TierX.yaml`)

| Parameter              | Description                                                                 | Valid Values                                     |
|------------------------|-----------------------------------------------------------------------------|--------------------------------------------------|
| applications           | Applications to simulate                                                    | NN, Seizure, SpikeSorting, GRU                   |
| optimize_metrics       | Metrics to optimize during design space exploration                         | throughput, latency, operatingtime, implant_power |
| sweep_types            | Types of parameter sweeps to perform                                        | communication, power, node                       |
| communication_methods  | Communication methods between tiers                                         | HIGH-BCC, LOW-BCC, LOW-RF                        |
| power_sources          | Energy storage types                                                        | OFF, SMALL-CAP, BAT, SMALL-BAT                   |
| node_placements        | Placement of nodes                                                          | NECK-ARM, NECK-EXTERNAL, TEMPLE-ARM              |
| power_constraints      | Safety power constraints per node type (peak/average in mW)                 | See `TierX.yaml`                                 |
| input_scaling          | Input-dependent scaling for compute latency and power                       | See `TierX.yaml`                                 |
| pipelining             | Computation-communication pipelining configuration                          | See `TierX.yaml`                                 |
| sram_model             | On-chip SRAM modeling based on CACTI                                        | See `TierX.yaml`                                 |

### Low-Level DSE Parameters (`SearchSpace.yaml`)

| Parameter              | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| component_generation   | Hardware component creation settings (TRX, processor, power)                |
| dse.electrodes         | Electrode count ranges per application and metric                           |
| dse.charge_times       | Charge time sweep values (hours)                                            |
| dse.ga                 | Genetic algorithm parameters (pop_size, generations, crossover, mutation)   |
| dse.pruning            | Pruning conditions for rejecting simulation results                         |

## Output and Results

- **Results Directory**: `results/`
  - 3D plots (SVG files)
  - Raw data (CSV files)
  - `separated/` subdirectory with per-sweep-option CSV files
- **Data Directory**: `data_DSE/`
  - Simulation data (PKL files)
  - Intermediate results
  - Best solution summaries (JSON, when using GA search)


## Project Structure
```
TierX/
├── run.sh                   # Main execution script
├── install.sh               # Dependency installation script
├── TierX.yaml               # High-level user configuration file
├── SearchSpace.yaml         # Low-level DSE parameter configuration
├── scripts_case_study/      # Optional case-study helper scripts
├── README.md                # This documentation
├── results/                 # Simulation output and analysis
├── data_DSE/                # Design space exploration data
├── src/                     # Internal simulation scripts
│   ├── DSE.py               # Design space exploration engine
│   ├── simulate.py          # Core simulation runner
│   ├── create_components.py # Hardware component YAML generator
│   ├── generate_all_configs.py  # Configuration file generator
│   ├── integrity_check.py   # YAML file integrity checker
│   ├── esp_report_parser.py # ESP HLS synthesis report parser
│   ├── Plot_graph.py        # 3D plot generation
│   ├── analyze_results.py   # Result analysis and consolidation
│   ├── analyze_pes.py       # PE pipeline analysis tool
│   ├── split_csv_results.py # CSV result splitter
│   ├── dse_stats.py         # DSE statistics calculator
│   ├── summarize_best_solutions.py  # Best solution aggregator
│   └── profiler.py          # Power/energy profiling module
└── lib/                     # Core simulation libraries
    ├── Input/               # Input configuration files
    │   └── PEs.yaml         # Processing element definitions
    ├── Network/             # Network simulation modules
    ├── Node/                # Node architecture modules
    └── PropagationChannel/  # Channel modeling modules
```


## Adding a New Application

To add a custom application (e.g. `MyApp`) to TierX, the following files must be modified or created. The example below assumes a pipeline of 3 MAC kernels split across 3 tiers.

### Step 1: Define the Application Pipeline in `lib/Input/PEs.yaml`

Add a new entry under `Application:` that specifies the kernel pipeline, output dimensions, strides, scaling factors, and SRAM access patterns.

```yaml
Application:
  # ... existing apps ...
  - name: MyApp
    pipeline: [MAC, MAC, MAC]          # Kernel sequence (PEs from the PEs section)
    dimensions: [[100,1], [100,1], [100,1]]  # [output_dim, 1] per kernel
    strides: [1, 1, 1]                       # Samples between kernel invocations
    scaling: [100, 100, 100]                 # Input-dependent scaling factor per kernel
    sram_accesses: [[2,1], [2,1], [2,1]]     # [reads, writes] per kernel execution
```

Each PE referenced in the pipeline (e.g. `MAC`) must have an entry in the `PEs:` section of the same file with non-zero power values:

```yaml
PEs:
  MAC:
    max_freq_mhz: 80.0
    power:
      static: 50.0    # μW
      dynamic: 1.5     # μW
    latency_ms: 0.00135
    area_kge: 27.81
```

### Step 2: Create a Base Workload Config

Create `lib/Input/MyApp/BASE_MyApp.yaml` — a base YAML config that defines the 3-tier hardware spec (implant, near-implant, off-body), communication links, power management, sensor, and environment. This template will be used by `generate_all_configs.py` to generate all sweep combinations.

See existing base files for reference: `lib/Input/NN/BASE_NN.yaml`, `lib/Input/Seizure/BASE_Seizure.yaml`.

The base config must include `processor: null` for all nodes (processor configs are generated separately by `create_components.py`).

### Step 3: Register the Application in Source Files

The following source files contain hardcoded application lists that must be updated:

| File | Location | What to Add |
|------|----------|-------------|
| `run.sh` | `VALID_APPS` array | `"MyApp"` |
| `src/DSE.py` | `APPLICATION_TYPES` list | `'MyApp'` |
| `src/create_components.py` | `predefined_application_types` list | `'MyApp'` |
| `src/create_components.py` | `APP_PROC_CONFIG` dict | `'MyApp': (output_offset, output_stride, input_timesteps)` |
| `src/generate_all_configs.py` | `APP_GENERATORS` dict | `"MyApp": ("lib/Input/MyApp/BASE_MyApp.yaml", "MyApp", "My Application")` |

#### `run.sh`
```bash
local VALID_APPS=("NN" "Seizure" "SpikeSorting" "GRU" "MyApp")
```

#### `src/DSE.py`
```python
APPLICATION_TYPES = ['NN', 'Seizure', 'SpikeSorting', 'GRU', 'MyApp']
```

#### `src/create_components.py`
```python
predefined_application_types = ['NN', 'Seizure', 'SpikeSorting', 'GRU', 'MyApp']

# Inside compute_processor_entry():
APP_PROC_CONFIG = {
    'NN': (150, 1500, 4500),
    'Seizure': (4, 120, 120),
    'SpikeSorting': (4, 120, 120),
    'GRU': (100, 3000, 3000),
    'MyApp': (100, 1000, 1000),  # (output_offset, output_stride, input_timesteps_per_output)
}
```

#### `src/generate_all_configs.py`
```python
APP_GENERATORS = {
    # ... existing entries ...
    "MyApp": ("lib/Input/MyApp/BASE_MyApp.yaml", "MyApp", "My Application"),
}
```

### Step 4: Add to `TierX.yaml`

Add the application name to the `applications` list:
```yaml
applications:
  - "NN"
  - "Seizure"
  - "SpikeSorting"
  - "GRU"
  - "MyApp"
```

Optionally, add application-specific electrode ranges in `SearchSpace.yaml`:
```yaml
dse:
  electrodes:
    throughput:
      MyApp: [100, 200, 300, 400, 500]
```

### Step 5: Generate Configs and Run

```bash
# Generate all sweep configs for the new application
python3 src/generate_all_configs.py --apps MyApp

# Run the full simulation
./run.sh
```

### Quick Test (without DSE)

To quickly verify the new application works without running the full DSE, create a single workload config with manually assigned processors and run `simulate.py` directly:

1. Create `lib/Input/MyApp/NECK-ARM_HIGH-BCC_OFF_MyApp_111.yaml` with processor fields filled in (including `kernel_strides`, `kernel_dimensions`, `kernel_latencies`, `kernel_powers`, `kernel_sram_accesses`, `num_kernels`)
2. Update `src/run.yaml`:
   ```yaml
   workloads:
   - MyApp/NECK-ARM_HIGH-BCC_OFF_MyApp_111
   ```
3. Run:
   ```bash
   python src/simulate.py
   ```

## ESP HLS Integration

TierX supports an optional integration with [ESP (Embedded Scalable Platform)](https://www.esp.cs.columbia.edu/) to replace manually-specified PE parameters with values obtained from actual HLS (High-Level Synthesis) results.

### Overview

When a user implements a BCI kernel in C++ and synthesizes it through ESP's Stratus HLS + Vivado flow, the resulting hardware metrics (frequency, power, latency, area) can be fed back into TierX to replace the corresponding PE entry in `PEs.yaml`.

**By default, ESP integration is disabled** (`esp.enabled: false` in `PEs.yaml`). Existing pipelines (NN, Seizure, SpikeSorting, GRU) are completely unaffected.

### Architecture & Interface

**ESP Docker** (requires Vivado + Cadence licenses)
1. Write C++ kernel (e.g. `mac.cpp`)
2. Run Stratus HLS synthesis
3. Run Vivado FPGA synthesis (optional, for power)

Outputs: `stratus_hls.log`, `scheduler.rpt`, `mac.xml`, Vivado reports

&darr; &ensp; `esp_report_parser.py --docker --update-pe`

**TierX Side**
1. `PEs.yaml` updated with synthesized values + `esp.enabled: true`
2. `create_components.py` &rarr; `DSE.py` &rarr; Simulation
   - Validates power, applies `batching_factor`, uses PLM sizes for SRAM buffer override

### Workflow

> **License note:** Cadence Stratus HLS and Xilinx Vivado licenses are required for C++→RTL and RTL→FPGA synthesis respectively. TierX itself requires no license.

#### Step 1: Synthesize in ESP Docker

```bash
# Inside the ESP Docker container
cd /root/esp/accelerators/stratus_hls/mac_stratus/hw
make mac_stratus-hls      # Stratus HLS synthesis
# (optional) Vivado synthesis for power data is triggered automatically
```

#### Step 2: Update PEs.yaml

```bash
# From the host machine (with Docker access)
cd tierx/
python3 src/esp_report_parser.py \
  --docker esp_workspace \
  --accel mac_stratus \
  --config BASIC_DMA32 \
  --update-pe MAC
```

This extracts HLS metrics from the Docker container and directly updates the MAC entry in `PEs.yaml` with synthesized values (frequency, power, latency, area).

#### Step 3: Enable ESP in PEs.yaml

Set `esp.enabled: true` for the PE to activate ESP-specific behavior during simulation:

```yaml
esp:
  enabled: true   # Activates: batching_factor scaling, PLM buffer override, power validation
```

#### Step 4: Run TierX simulation

```bash
./run.sh   # MAC PE values are now based on actual HLS results
```

> **Note:** The MAC PE must be referenced by an application pipeline in `PEs.yaml` to have an effect on simulation. Define a new pipeline under `Application:` that includes MAC as a kernel.

### PEs.yaml ESP Entry Format

ESP-enabled PEs have an additional `esp:` section alongside standard fields:

```yaml
PEs:
  MAC:
    max_freq_mhz: 80.0           # ← Updated by esp_report_parser.py
    power:
      static: 0.0                # ← Updated when Vivado power report is available
      dynamic: 0.0
    latency_ms: 0.00135           # ← Computed from HLS cycle count × clock period
    area_kge: 27.81               # ← Estimated from LUT count (1 LUT ≈ 6 GE)
    esp:
      enabled: false              # Set to true after importing ESP synthesis results
      source: mac_stratus         # ESP accelerator directory name
      data_bitwidth: 32
      registers:                  # HLS-configurable registers
        - {name: mac_n, default: 1, max: 16}
        - {name: mac_vec, default: 100, max: 100}
        - {name: mac_len, default: 64, max: 64}
      chunk_factor: {input: 6400, output: 100}   # PLM sizes (words)
      batching_factor: 1
      in_place: false
      input_size_expr: "mac_len * mac_vec * mac_n"
      output_size_expr: "mac_vec * mac_n"
```

> Run `python3 src/esp_report_parser.py --help` for full CLI usage.