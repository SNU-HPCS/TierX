"""Plot graph from existing pickle file - New unified version."""
import os
import pickle
import sys
from mpl_toolkits.mplot3d import Axes3D
import numpy as np
from scipy.interpolate import griddata
import subprocess
import shutil
import csv

# Helper to crop SVG whitespace using Inkscape (if available)
def crop_svg_whitespace(svg_path: str) -> bool:
    """Crop SVG to the drawing area using Inkscape. Returns True on success."""
    inkscape = shutil.which('inkscape')
    if not inkscape:
        print('Inkscape not found; skipping external SVG crop.')
        return False

    tmp_out = svg_path + '.tmp.svg'
    variants = [
        # Inkscape ≥ 1.0
        [inkscape, svg_path, '--export-area-drawing', '--export-type=svg', f'--export-filename={tmp_out}'],
        # Alternate modern syntax
        [inkscape, '--export-area-drawing', '--export-plain-svg', svg_path, f'--export-filename={tmp_out}'],
        # Legacy Inkscape (0.92) no-GUI; use equals for output
        [inkscape, '-z', '--export-area-drawing', f'--export-plain-svg={tmp_out}', svg_path],
        # Legacy Inkscape (0.92) explicit --file
        [inkscape, '--without-gui', '--file', svg_path, '--export-area-drawing', f'--export-plain-svg={tmp_out}'],
    ]
    for cmd in variants:
        try:
            completed = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(tmp_out):
                os.replace(tmp_out, svg_path)
                print(f'Cropped SVG saved: {svg_path}')
                return True
        except Exception as e:
            continue
    # Cleanup if something failed
    try:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
    except Exception:
        pass
    print('Failed to crop SVG with Inkscape; leaving original file.')
    return False

graphs = []

if len(sys.argv) > 4:
    desired_application = sys.argv[1]
    assert desired_application in ['NN', 'Seizure', 'SpikeSorting', 'GRU'], f'Invalid application: {desired_application}. '
    workloads = sys.argv[4:]
else:
    assert( len(sys.argv) > 1), "Please provide at least one workload."

component_types = sys.argv[2] if len(sys.argv) > 2 else 'trx'

sweep = None
optimize_metric = sys.argv[3]

# map the desired offloading map for latency and power breakdowns, this is used only for display purposes
offloading_map = {
    'num_electrode': 200,  # fix the electrodes to 200
}

is_throughput_breakdown = False
is_latency_breakdown = False
is_power_breakdown = False

# Unified setup for all component types - now they all work the same way
if component_types == 'trx':
    sweep = 'communication'
elif component_types == 'power':
    sweep = 'power'
elif component_types == 'env':
    sweep = 'node_placement'


opt_type = optimize_metric

data_dir = 'data_DSE'

# Determine search strategy subdirectory (default: exhaustive)
search_strategy = os.environ.get('DSE_SEARCH', 'exhaustive')
data_subdir = os.path.join(data_dir, search_strategy)

# Load the graphs from the pickle files
for workload in workloads:
    pickle_file = f'{data_subdir}/{workload}_{component_types}_{opt_type}_graph.pkl'
    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as f:
            graphs.append(pickle.load(f))
    else:
        print(f'No graph found for {workload}_{component_types}_{opt_type}_graph. Please run the DSE script first to generate the graph.')
        graphs.append([])  # Append an empty list if no graph found

print(f'Loaded {len(graphs)} graphs for component type: {component_types}')
print(f'Workload length: {len(workloads)}, graphs length: {len(graphs)}')

result_dir = f'results'
if not os.path.exists(result_dir):
    os.makedirs(result_dir)
if not os.path.exists(f'{result_dir}/plots'):
    os.makedirs(f'{result_dir}/plots')

# Plotting the graph
import matplotlib.pyplot as plt
import matplotlib as mpl

# Set global tick width parameters for thick tick marks

thick_line_width = 8
thin_line_width = 5
ticks_font_size = 100
labels_font_size = 100
marker_size = 800

mpl.rcParams['xtick.major.width'] = thick_line_width
mpl.rcParams['ytick.major.width'] = thick_line_width
mpl.rcParams['xtick.minor.width'] = thick_line_width
mpl.rcParams['ytick.minor.width'] = thick_line_width
mpl.rcParams['legend.fontsize'] = labels_font_size * 0.5


if component_types in ['trx', 'power', 'env']:
    import re                                   # regex for number extraction
    import numpy as np
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D     # noqa: F401 – needed for 3-D
    import itertools

    total_workload_parts = -1

    processed_data = []

    data_dict = {}
    breakdown_data = {}
    # initial data structure to hold the data
    for idx, graph in enumerate(graphs):
        if not graph:
            continue  # Skip empty graphs
        workload = workloads[idx]
        # Extract from workload name: {env_type}_{comm_type}_{power_type}
        env_type = workload.split('_')[0]
        comm_type = workload.split('_')[1]
        power_type = workload.split('_')[2]

        # verify that the workload is in the correct format
        if len(workload.split('_')) < 3:
            print(f"❌ Invalid workload format: {workload}. Expected format: <env_type>_<comm_type>_<power_type>.")
            continue
        if env_type not in ['NECK-ARM', 'NECK-EXTERNAL', 'TEMPLE-ARM']:
            print(f"❌ Invalid environment type: {env_type}. Expected one of: NECK-ARM, NECK-EXTERNAL, TEMPLE-ARM.")
            continue
        if comm_type not in ['HIGH-BCC', 'LOW-BCC', 'LOW-RF']:
            print(f"❌ Invalid communication type: {comm_type}. Expected one of: HIGH-BCC, LOW-BCC, LOW-RF.")
            continue
        if power_type not in ['OFF', 'SMALL-CAP', 'BAT', 'SMALL-BAT']:
            print(f"❌ Invalid power type: {power_type}. Expected one of: OFF, SMALL-CAP, BAT, SMALL-BAT.")
            continue

        try:
            implant_jobs, near_jobs, off_jobs = map(int, re.findall(r'\d', workload))
            if total_workload_parts == -1:
                total_workload_parts = implant_jobs + near_jobs + off_jobs
        except ValueError as e:
            print(f"❌ {e}, {workload}")
            continue

        nr = near_jobs
        or_ = off_jobs

        key = (comm_type, power_type, env_type, nr, or_)

        for point in graph:
            BER, num_elec, p_violate, avg_p_violate, l_violate, component_file, lifetime, latency, implant_power_consumption, total_power_breakdown, latency_breakdown = point
            
            if (optimize_metric == 'throughput' and is_throughput_breakdown) or (optimize_metric == 'latency' and is_latency_breakdown) or (optimize_metric == 'operatingtime' and is_power_breakdown) or (optimize_metric == 'implant_power' and is_latency_breakdown) or (optimize_metric == 'implant_power' and is_power_breakdown):
                # for power/latency breakdown data
                offloading_score = implant_jobs + 10 * near_jobs + 100 * off_jobs
                config_key = (comm_type, env_type, power_type)

                # Initialize config_key if not exists
                if config_key not in breakdown_data:
                    breakdown_data[config_key] = []

                # Store power breakdown data for this point
                breakdown_data[config_key].append({
                    'implant_jobs': implant_jobs,
                    'near_jobs': near_jobs,
                    'off_jobs': off_jobs,
                    'offloading_score': offloading_score,
                    'num_elec': num_elec,
                    'total_power_breakdown': total_power_breakdown,
                    'latency_breakdown': latency_breakdown,
                    'total_latency': latency,
                    'lifetime': lifetime,
                    'implant_power_consumption': implant_power_consumption,
                    'power_violation': p_violate,
                    'latency_violation': l_violate
                })
            
            if p_violate or l_violate:
                continue

            # Completely unified sweep type determination - all work exactly the same way
            if sweep == 'communication':
                sweep_type = comm_type  # For communication, use comm_type (RF/BCC)
            elif sweep == 'power':
                sweep_type = power_type  # For power, use power_type (BAT/CAP)
            elif sweep == 'node_placement':
                sweep_type = env_type   # For node_placement, use env_type (CLOSE/FAR)

            # Use (comm_type, power_type, env_type, nr, or_) as key
            if optimize_metric == 'throughput':
                data_dict[key] = max(data_dict.get(key, 0), num_elec * 0.48) # Convert into Mbps (30KHz, 16bits)
            elif optimize_metric == 'latency':
                if key in data_dict:
                    if data_dict[key] != 1/latency:
                        sys.__stdout__.write(f'🚨 Key: {key}, latency: {data_dict[key]} --> {1/latency}\n')
                        sys.__stdout__.flush()
                        # assert if error exceeds 0.1%
                        assert abs(data_dict[key] - 1/latency) < 0.001, f"Latency mismatch for key {key}: {data_dict[key]} vs {1/latency}"
                data_dict[key] = max(data_dict.get(key, 0), 1/latency)
            elif optimize_metric == 'operatingtime':
                data_dict[key] = max(data_dict.get(key, 0), lifetime)
            elif optimize_metric == 'implant_power':
                if key in data_dict:
                    if data_dict[key] != 1/implant_power_consumption:
                        sys.__stdout__.write(f'🚨 Key: {key}, implant_power_consumption: {data_dict[key]} --> {1/implant_power_consumption}\n')
                        sys.__stdout__.flush()
                        # assert if error exceeds 0.1%
                        assert abs(data_dict[key] - 1/implant_power_consumption) < 0.001, f"Implant power mismatch for key {key}: {data_dict[key]} vs {1/implant_power_consumption}"
                data_dict[key] = max(data_dict.get(key, 0), 1/implant_power_consumption)

    # Unified processing for all sweep types - they now all work the same way
    all_comm_types = set()
    all_power_types = set()
    all_env_types = set()

    for workload in workloads:
        # Extract from workload name: {env_type}_{comm_type}_{power_type}
        env = workload.split('_')[0]     # CLOSE, FAR
        comm = workload.split('_')[1]    # RF, BCC
        power = workload.split('_')[2]   # BAT, CAP
        all_comm_types.add(comm)
        all_power_types.add(power)
        all_env_types.add(env)

            # verify that the workload is in the correct format
        if len(workload.split('_')) < 3:
            print(f"❌ Invalid workload format: {workload}. Expected format: <env_type>_<comm_type>_<power_type>.")
            continue
        if env not in ['NECK-ARM', 'NECK-EXTERNAL', 'TEMPLE-ARM']:
            print(f"❌ Invalid environment type: {env}. Expected one of: NECK-ARM, NECK-EXTERNAL, TEMPLE-ARM.")
            continue
        if comm not in ['HIGH-BCC', 'LOW-BCC', 'LOW-RF']:
            print(f"❌ Invalid communication type: {comm}. Expected one of: HIGH-BCC, LOW-BCC, LOW-RF.")
            continue
        if power not in ['OFF', 'SMALL-CAP', 'BAT', 'SMALL-BAT']:
            print(f"❌ Invalid power type: {power}. Expected one of: OFF, SMALL-CAP, BAT, SMALL-BAT.")
            continue

    # Convert sets to sorted lists
    all_comm_types = sorted(all_comm_types)
    all_power_types = sorted(all_power_types)
    all_env_types = sorted(all_env_types)
    
    print(f"All communication types: {all_comm_types}"
          f"\nAll power types: {all_power_types}"
          f"\nAll environment types: {all_env_types}")

    if sweep == 'communication':
        desired_sweep_types = all_comm_types
    elif sweep == 'power':
        desired_sweep_types = all_power_types
    elif sweep == 'node_placement':
        desired_sweep_types = all_env_types
    
    # Define color schemes for each sweep type
    color_schemes = {
        'communication': ['indigo', 'red', 'darkgreen'],
        'power': ['teal', 'sienna', 'maroon', 'olive'],
        'node_placement': ['magenta', 'green', 'navy']
    }
    
    # Define combinations to loop over (excluding the sweep dimension)
    if sweep == 'communication':
        combinations = [(power_type, env_type, None) for power_type in all_power_types for env_type in all_env_types]
        combination_labels = lambda p, e, c: f"Environment: {e}, Power: {p}"
        file_suffix = lambda p, e, c: f"{e}_{p}"
        title_info = lambda p, e, c: f"Environment: {e}\nPower: {p}\n"
    elif sweep == 'power':
        combinations = [(None, env_type, comm_type) for comm_type in all_comm_types for env_type in all_env_types]
        combination_labels = lambda p, e, c: f"Communication: {c}, Environment: {e}"
        file_suffix = lambda p, e, c: f"{c}_{e}"
        title_info = lambda p, e, c: f"Environment: {e}\nCommunication: {c}\n"
    elif sweep == 'node_placement':
        combinations = [(power_type, None, comm_type) for comm_type in all_comm_types for power_type in all_power_types]
        combination_labels = lambda p, e, c: f"Communication: {c}, Power: {p}"
        file_suffix = lambda p, e, c: f"{c}_{p}"
        title_info = lambda p, e, c: f"Power: {p}\nCommunication: {c}\n"
    
    def create_plot(power_type, env_type, comm_type):
        """Create a single plot for the given combination."""
        # For legend & file names 
        desired_sweep_types_list = list(desired_sweep_types)
        desired_sweep_types_list_ = [pt.replace(' ', '') for pt in desired_sweep_types_list]
        desired_sweep_types_list_ = ','.join(desired_sweep_types_list_)
        desired_sweep_types_list_ = desired_sweep_types_list_.replace(',', '_')

        # Unified filtering and naming
        print(f"\nSweeping {sweep.capitalize()} Types: {desired_sweep_types_list} for {combination_labels(power_type, env_type, comm_type)}")
        result_file_name = f'{desired_application}_optimize_{optimize_metric}_sweeping_{sweep}_{desired_sweep_types_list_}_{file_suffix(power_type, env_type, comm_type)}'
        
        sweep_filtered_data = set()

        # Filter data based on sweep type
        if sweep == 'communication':
            sweep_filtered_data = { (comm, nr, or_, z) for (comm, power, env, nr, or_), z in data_dict.items()
                                    if power_type == power and env_type == env }
        elif sweep == 'power':
            sweep_filtered_data = { (power, nr, or_, z) for (comm, power, env, nr, or_), z in data_dict.items()
                                    if comm_type == comm and env_type == env }
        elif sweep == 'node_placement':
            sweep_filtered_data = { (env, nr, or_, z) for (comm, power, env, nr, or_), z in data_dict.items()
                                    if comm_type == comm and power_type == power }

        # Create a new figure for each parameter combination with ultra-high resolution
        fig = plt.figure(figsize=(25, 23), dpi=600)
        ax = fig.add_subplot(111, projection='3d')

        # Set up the axes labels with Arial font, larger sizes, and more spacing
        ax.set_xlabel('Near-implant (%)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * 1.3)
        ax.set_ylabel('Off-implant (%)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * 1.5)

        # Make sure z-axis has enough space
        ax.margins(0.1)  # Add margins around the plot
        
        # Configure tick labels with Arial font and thick tick marks
        ax.tick_params(axis='x', labelsize=ticks_font_size, width=10, length=30, pad=ticks_font_size * 0.3)
        ax.tick_params(axis='y', labelsize=ticks_font_size, width=10, length=30, pad=ticks_font_size * 0.5)

        # Set tick label font to Arial
        for tick in ax.get_xticklabels():
            tick.set_fontfamily('Arial')
            tick.set_fontsize(ticks_font_size)
        for tick in ax.get_yticklabels():
            tick.set_fontfamily('Arial')
            tick.set_fontsize(ticks_font_size)
        for tick in ax.get_zticklabels():
            tick.set_fontfamily('Arial')
            tick.set_fontsize(ticks_font_size)
        
        # Axes lines
        ax.xaxis.line.set_linewidth(thick_line_width)
        ax.yaxis.line.set_linewidth(thick_line_width)
        ax.zaxis.line.set_linewidth(thick_line_width)
        ax.xaxis.pane.set_linewidth(thick_line_width)
        ax.yaxis.pane.set_linewidth(thick_line_width)
        ax.zaxis.pane.set_linewidth(thick_line_width)

        # Grid lines (grey) 
        ax.xaxis._axinfo['grid'].update({'linewidth': thin_line_width})
        ax.yaxis._axinfo['grid'].update({'linewidth': thin_line_width})
        ax.zaxis._axinfo['grid'].update({'linewidth': thin_line_width})

        # Get colors for this sweep type
        solid_colors = color_schemes[sweep]

        result_file = f'{result_dir}/{result_file_name}.csv'

        # Remove the result file if it exists
        if os.path.exists(result_file):
            os.remove(result_file)

        color_idx = 0
        overall_min = -1
        overall_max = -1
        
        # Unified processing for all sweep types
        for option in desired_sweep_types:
            print(f"\n{sweep.capitalize()} Type: {option}, Total Workload Parts: {total_workload_parts}")

            filtered_data = { (nr, or_): z for st, nr, or_, z in sweep_filtered_data if st == option }
            if not filtered_data:
                print(f"No data for {sweep} Type: {option}, Total Workload Parts: {total_workload_parts}")
                with open(result_file, 'a') as f:
                    f.write(f"\n{sweep.capitalize()} Type: {option}, Total Workload Parts: {total_workload_parts}\n")
                    f.write("No data available for this sweep type.\n")
                continue

            nr_vals = range(0, total_workload_parts + 1)
            or_vals = range(0, total_workload_parts + 1)
            
            # Build meshgrid
            NR, OR = np.meshgrid(nr_vals, or_vals)
            NR_percent = NR / total_workload_parts * 100
            OR_percent = OR / total_workload_parts * 100

            # Only use original filtered_data to fill Z
            Z = np.full_like(NR, np.nan, dtype=float)

            # Correctly fill Z for each mesh cell
            for i in range(OR.shape[0]):
                for j in range(NR.shape[1]):
                    nr_val = NR[i, j]
                    or_val = OR[i, j]
                    key    = (nr_val, or_val)
                    if key in filtered_data:
                        Z[i, j] = filtered_data[key]
                    elif i + j <= total_workload_parts:
                        Z[i, j] = 0.0  # Fill with 0.0 if not in filtered_data and within bounds

            color = solid_colors[color_idx % len(solid_colors)]
            label = f"{option}"

            ax.plot_surface(NR_percent, OR_percent, Z,
                            color=color, edgecolor='k', linewidth=thin_line_width,
                            alpha=0.35, antialiased=True)
            mask = ~np.isnan(Z)
            ax.scatter(NR_percent[mask], OR_percent[mask], Z[mask],
                    color=color, s=marker_size, label=label)

            with open(result_file, 'a') as f:
                f.write(f"\n{sweep.capitalize()} Type: {label}, Total Workload Parts: {total_workload_parts}\n")
                f.write(f"\nNR,OR,{optimize_metric.capitalize()}\n")
                for i in range(NR.shape[0]):
                    for j in range(NR.shape[1]):
                        nr_val, or_val, z_val = NR[i,j], OR[i,j], Z[i,j]
                        if (nr_val, or_val) in filtered_data:
                            f.write(f"{nr_val:.5f},{or_val:.5f},{z_val:.5f}\n")

            max_z = np.nanmax(Z)
            if max_z > overall_max or overall_max == -1:
                overall_max = max_z
            min_z = np.nanmin(Z)
            if min_z < overall_min or overall_min == -1:
                overall_min = min_z

            max_indices = np.argwhere(np.isclose(Z, max_z))
            max_coords = [(NR[i, j], OR[i, j]) for i, j in max_indices] 
            max_coords = [(int(nr_val), int(or_val)) for nr_val, or_val in max_coords]

            with open(result_file, 'a') as f:
                f.write(f"\nMax {optimize_metric},{max_z:.5f}\nMax coordinates,{max_coords}\n")

            print(f"{sweep.capitalize()} Type: {option}")
            print(f"Max {optimize_metric}: {max_z:.5f}\nMax coordinates: {max_coords}")

            # For these points, emphasize their importance
            for nr_val, or_val in max_coords:
                ax.scatter(nr_val / total_workload_parts * 100, or_val / total_workload_parts * 100, max_z - (max_z - min_z) * 0.0001, color='khaki', s=marker_size*3)

            color_idx += 1


        num_digits = len(str(int(overall_max)))
        if optimize_metric == 'power':
            num_digits = 2
        else:
            if 0.2 < overall_max < 1:
                num_digits = 3
            elif 0.02 < overall_max <= 0.2:
                num_digits = 4
            elif 0.002 < overall_max <= 0.02:
                num_digits = 5
            elif overall_max <= 0.002:
                num_digits = 6

        pad_size = num_digits * 0.2 if num_digits > 0 else 0.3

        # Add z-axis label
        if optimize_metric == 'throughput':
            ax.set_zlabel('Throughput\n(Mbps)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.4 + pad_size))
        elif optimize_metric == 'latency':
            ax.set_zlabel('Speed (1/ms)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.0 + pad_size))
        elif optimize_metric == 'operatingtime':
            ax.set_zlabel('Operating\ntime (hrs)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.4 + pad_size))
        elif optimize_metric == 'implant_power':
            ax.set_zlabel('Implant Power\nefficiency (1/mW)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.4 + pad_size))

        ax.tick_params(axis='z', labelsize=ticks_font_size, width=10, length=30, pad=ticks_font_size * (0.4 + pad_size * 0.5))

        if optimize_metric == 'power':
            ax.set_zticks([0, 6, 12, 18, 24])
    
        # remove background
        ax.xaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))
        ax.yaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))
        ax.zaxis.set_pane_color((1.0, 1.0, 1.0, 0.0))

        # Prepare title (legend and extra right margin will be added after the first save)
        title = f'Offloading vs {optimize_metric.capitalize()}\n' \
                f'Sweep {sweep.capitalize()}: {desired_sweep_types_list_}\n' \
                f'=========================\n' \
                f'Application: {desired_application}\n' \
                f'{title_info(power_type, env_type, comm_type)}'

        # Flip the axes for better visualization
        ax.view_init(elev=30, azim=-60)
        ax.set_xlim(left=100, right=0)
        ax.set_ylim(bottom=0, top=100)
        if optimize_metric == 'power':
            ax.set_zlim(bottom=0, top=24)
        else:
            ax.set_zlim(bottom=0, top=1.1 * overall_max)
        # Squeeze the z-axis height by scaling the aspect ratio
        ax.get_proj = lambda: np.dot(Axes3D.get_proj(ax), np.diag([1, 1, 0.6, 1]))

        # First save: version without title and legend – tightly crop via Matplotlib, preserving labels
        no_title_path = f'{result_dir}/plots/{result_file_name}.svg'
        fig.patch.set_alpha(0.0)  # transparent figure background
        ax.patch.set_alpha(0.0)   # transparent axes background
        fig.savefig(
            no_title_path,
            dpi=600,
            bbox_inches='tight',
            pad_inches=0.02,
        )
        # Optional: try further cropping using Inkscape if available
        if crop_svg_whitespace(no_title_path):
            print(f'Saved cropped plot (no title/legend) as {no_title_path}')
        else:
            print(f'Saved tightly-cropped plot (no title/legend) as {no_title_path}')

        # Leave space on the right and move legend to the figure's top-right corner
        fig.subplots_adjust(right=0.7, bottom=0.08)
        
        # Now add legend to the figure's top-right corner
        ax.legend(
            loc='lower left',              # anchor legend's upper-left corner
            bbox_to_anchor=(1.1, 1.1),    # at the top-right outside the axes
            framealpha=0.9,
            prop={'family': 'Arial', 'weight': 'bold'},
        )

        # Add z-axis label
        if optimize_metric == 'throughput':
            ax.set_zlabel('Throughput\n(Mbps)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.3 + pad_size))
        elif optimize_metric == 'latency':
            ax.set_zlabel('Speed (1/ms)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1 + pad_size))
        elif optimize_metric == 'operatingtime':
            ax.set_zlabel('Operating\ntime (hrs)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.3 + pad_size))
        elif optimize_metric == 'implant_power':
            ax.set_zlabel('Implant Power\nefficiency (1/mW)', fontfamily='Arial', fontsize=labels_font_size, labelpad=labels_font_size * (1.3 + pad_size))


        ax.tick_params(axis='x', labelsize=ticks_font_size * 0.8, width=10, length=30, pad=ticks_font_size * 0.8 * 0.3)
        ax.tick_params(axis='y', labelsize=ticks_font_size * 0.8, width=10, length=30, pad=ticks_font_size * 0.8 * 0.5)
        ax.tick_params(axis='z', labelsize=ticks_font_size * 0.8, width=10, length=30, pad=ticks_font_size * 0.8 * (0.4 + pad_size * 0.5))

        # No transparency for the final save
        fig.patch.set_alpha(1.0)  # solid figure background
        ax.patch.set_alpha(1.0)   # solid axes background

        ax.get_proj = lambda: np.dot(Axes3D.get_proj(ax), np.diag([1, 1, 0.8, 1]))

        # Save the plot with a descriptive name and ultra-high resolution
        ax.set_title(title, fontfamily='Arial', fontsize=labels_font_size * 0.5, fontweight='bold')
        
        # Save full version with title and legend (also transparent)
        plt.savefig(f'{result_dir}/{result_file_name}.svg', dpi=600, bbox_inches=None, pad_inches=0.5)
        print(f'Saved plot as {result_dir}/{result_file_name}.svg')

        plt.close()

    def create_throughput_breakdown(power_type, env_type, comm_type):
        # Unified processing for all sweep types
        for option in desired_sweep_types:
            # Find maximum achievable throughput from data_dict
            max_throughput_from_data_dict = 0
            if data_dict:
                max_throughput_from_data_dict = max(z for (st, nr, or_), z in data_dict.items() 
                                                    if st == option)

            print(f"Maximum achievable throughput from data_dict: {max_throughput_from_data_dict} electrodes")

            filtered_points = []
            for config_key, data_points in breakdown_data.items():
                if not data_points:
                    continue
                
                comm_type_key, env_type_key, power_type_key = config_key
                # Filter data points based on offloading_map constraints
                for point in data_points:
                    # Check if point matches offloading_map constraints
                    matches_constraints = True

                    if sweep == 'communication':
                        if comm_type_key != option and env_type_key != env_type and power_type_key != power_type:
                            matches_constraints = False
                    elif sweep == 'power':
                        if comm_type_key != comm_type and power_type_key != option and env_type_key != env_type:
                            matches_constraints = False
                    elif sweep == 'node_placement':
                        if comm_type_key != comm_type and power_type_key != power_type and env_type_key != option:
                            matches_constraints = False

                    if matches_constraints:
                        filtered_points.append(point)

            for point in filtered_points:
                point['is_optimal'] = (point['num_elec'] == max_throughput_from_data_dict and not point['latency_violation'] and not point['power_violation'])

            # save raw throughput data to csv
            throughput_csv_filename = None
            if sweep == 'communication':
                throughput_csv_filename = f'{result_dir}/throughput_raw_data_{desired_application}_{option}_{power_type}_{env_type}.csv'
            elif sweep == 'power':
                throughput_csv_filename = f'{result_dir}/throughput_raw_data_{desired_application}_{comm_type}_{option}_{env_type}.csv'
            elif sweep == 'node_placement':
                throughput_csv_filename = f'{result_dir}/throughput_raw_data_{desired_application}_{comm_type}_{power_type}_{option}.csv'

            if filtered_points:
                with open(throughput_csv_filename, 'w', newline='') as csvfile:
                    fieldnames = [
                        'implant_jobs', 'near_jobs', 'off_jobs', 'offloading_score',
                        'num_elec', 'is_optimal', 'latency_violation', 'power_violation'
                    ]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    # Filter dictionaries to only include fields that are in fieldnames
                    filtered_rows = [{key: point[key] for key in fieldnames if key in point} for point in filtered_points]
                    writer.writerows(filtered_rows)
                print(f'💾 Saved throughput raw data to: {throughput_csv_filename}')
                print(f'   Maximum achievable throughput from data_dict: {max_throughput_from_data_dict} electrodes')
                
                # Count optimal configurations (those matching data_dict maximum)
                optimal_configs = [entry for entry in filtered_points if entry['is_optimal']]
                
                print(f'   Number of optimal configurations (data_dict max): {len(optimal_configs)}')
                
                # Analyze violation patterns for optimal configurations
                if optimal_configs:
                    latency_violations = sum(1 for config in optimal_configs if config['latency_violation'])
                    power_violations = sum(1 for config in optimal_configs if config['power_violation'])
                    no_violations = sum(1 for config in optimal_configs if not config['latency_violation'] and not config['power_violation'])
                    
                    print(f'   At optimal throughput ({max_throughput_from_data_dict} electrodes):')
                    print(f'     - No violations: {no_violations} configurations')
                    print(f'     - Latency violations: {latency_violations} configurations')
                    print(f'     - Power violations: {power_violations} configurations')

    # Generate all plots using the unified function
    for power_type, env_type, comm_type in combinations:
        create_plot(power_type, env_type, comm_type)
        if optimize_metric == 'throughput' and is_throughput_breakdown:
            # For throughput breakdown, we need to draw 2D graphs
            print(f"comm_type: {comm_type}")
            print(f"env_type: {env_type}")
            print(f"power_type: {power_type}")
            create_throughput_breakdown(power_type, env_type, comm_type)

    # draw power breakdown for optimization metric 'power'
    if (optimize_metric == 'operatingtime' and is_power_breakdown) or (optimize_metric == 'implant_power' and is_power_breakdown):
        # Create power breakdown plots for each config_key
        for config_key, data_points in breakdown_data.items():
            if not data_points:
                continue
                
            comm_type, env_type, power_type = config_key
            
            # Filter data points based on offloading_map constraints
            filtered_points = []
            for point in data_points:
                # Check if point matches offloading_map constraints
                matches_constraints = True
                
                # Check num_electrode constraint
                if 'num_electrode' in offloading_map and point['num_elec'] != offloading_map['num_electrode']:
                    matches_constraints = False
                
                # Check off-implant constraint (extract from offloading_score)
                # offloading_score = implant_jobs + 10 * near_jobs + 100 * off_jobs
                # So off_jobs = offloading_score // 100, near_jobs = (offloading_score % 100) // 10
                if 'off' in offloading_map:
                    off_jobs = point['offloading_score'] // 100
                    if off_jobs != offloading_map['off']:
                        matches_constraints = False

                if matches_constraints:
                    filtered_points.append(point)
            
            if not filtered_points:
                print(f"⚠️ No data points match offloading_map constraints for {config_key}")
                continue
            
            # Create unique filename
            filename = f'power_breakdown_{desired_application}_{comm_type}_{env_type}_{power_type}'
            
            # Define the power components we want to plot
            power_components = ['sensor', 'processor', 'comm_link', 'pmu', 'charge_loss', 'charged_power']
            component_colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#FF9F43']

            # Extract power breakdown data for each node type (on, near, off)
            node_types = ['on', 'near', 'off']
            node_labels = ['Implant Node', 'Near Node', 'Off Node']
            
            # Create figure with subplots for each node type + total power
            fig, axes = plt.subplots(1, len(node_types) + 1, figsize=(6*(len(node_types) + 1), 8))
            if len(node_types) + 1 == 1:
                axes = [axes]
            
            # Calculate total power for each offloading configuration and save to CSV
            total_power_data = {}  # {offload_key: {'total_power': value, 'breakdown_by_node': {...}}}
            csv_data = []  # For CSV export
            
            # First pass: collect all data and calculate totals
            for point in filtered_points:
                breakdown = point['total_power_breakdown']
                if breakdown:
                    # Extract offloading distribution from offloading_score
                    score = point['offloading_score']
                    off_jobs = score // 100
                    remaining = score % 100
                    near_jobs = remaining // 10
                    implant_jobs = remaining % 10
                    
                    offload_key = (implant_jobs, near_jobs, off_jobs)
                    
                    # Calculate total power across all nodes and components
                    total_power = 0
                    node_powers = {}
                    component_totals = {}
                    node_component_breakdown = {}  # {node_type: {component: power}}
                    
                    for node_type in node_types:
                        if node_type in breakdown:
                            node_power = 0
                            node_breakdown = breakdown[node_type]
                            node_component_breakdown[node_type] = {}
                            
                            for component in power_components:
                                power_val = node_breakdown.get(component, 0)
                                node_power += power_val
                                total_power += power_val
                                
                                # Store individual node-component breakdown
                                node_component_breakdown[node_type][component] = power_val
                                
                                # Track component totals across all nodes
                                if component not in component_totals:
                                    component_totals[component] = 0
                                component_totals[component] += power_val
                            
                            node_powers[node_type] = node_power
                        else:
                            # Initialize empty breakdown for missing node types
                            node_component_breakdown[node_type] = {comp: 0 for comp in power_components}
                            node_powers[node_type] = 0
                    
                    # Store data for this configuration
                    if offload_key not in total_power_data:
                        total_power_data[offload_key] = {
                            'total_power': [],
                            'node_powers': {nt: [] for nt in node_types},
                            'component_totals': {comp: [] for comp in power_components},
                            'num_elec': point['num_elec'],
                            'implant_power_consumption': [],
                            'lifetime': [],
                            'power_violation': [],
                            'latency_violation': []
                        }
                    
                    total_power_data[offload_key]['total_power'].append(total_power)
                    for node_type in node_types:
                        total_power_data[offload_key]['node_powers'][node_type].append(node_powers.get(node_type, 0))
                    for component in power_components:
                        total_power_data[offload_key]['component_totals'][component].append(component_totals.get(component, 0))
                    total_power_data[offload_key]['implant_power_consumption'].append(point['implant_power_consumption'])
                    total_power_data[offload_key]['lifetime'].append(point['lifetime'])
                    total_power_data[offload_key]['power_violation'].append(point['power_violation'])
                    total_power_data[offload_key]['latency_violation'].append(point['latency_violation'])

                    # Add to CSV data
                    csv_row = {
                        'implant_jobs': implant_jobs,
                        'near_jobs': near_jobs,
                        'off_jobs': off_jobs,
                        'num_electrodes': point['num_elec'],
                        'total_power': total_power,
                        'power_violation': point['power_violation'],
                        'latency_violation': point['latency_violation'],
                        'implant_power_consumption': point['implant_power_consumption'],
                        'lifetime': point['lifetime']
                    }
                    
                    # Add individual node powers
                    for node_type in node_types:
                        csv_row[f'{node_type}_node_power'] = node_powers.get(node_type, 0)
                    
                    # Add component breakdown totals across all nodes
                    for component in power_components:
                        csv_row[f'total_{component}'] = component_totals.get(component, 0)
                    
                    # Add detailed breakdown for each node and component
                    for node_type in node_types:
                        for component in power_components:
                            column_name = f'{node_type}_{component}'
                            csv_row[column_name] = node_component_breakdown[node_type].get(component, 0)
                    
                    csv_data.append(csv_row)

            # Save CSV data
            csv_filename = f'{result_dir}/power_data_{desired_application}_{comm_type}_{env_type}_{power_type}.csv'
            import csv
            # Restrict to only the four requested configurations
            # 1) only on_jobs nonzero, 2) only near_jobs nonzero, 3) only off_jobs nonzero, 4) min average power
            on_only_key = None
            near_only_key = None
            off_only_key = None
            min_power_key = None
            min_power = float('inf')
            max_lifetime_key = 0
            for offload_key, data in total_power_data.items():
                on, near, off = offload_key
                if on > 0 and near == 0 and off == 0:
                    on_only_key = offload_key
                if on == 0 and near > 0 and off == 0:
                    near_only_key = offload_key
                if on == 0 and near == 0 and off > 0:
                    off_only_key = offload_key
                if min(data['implant_power_consumption']) < min_power and max(data['lifetime']) >= max_lifetime_key and not any(data['power_violation']) and not any(data['latency_violation']):
                    min_power = min(data['implant_power_consumption'])
                    min_power_key = offload_key
                    max_lifetime_key = max(data['lifetime'])

            selected_keys = [0,0,0,0]
            if on_only_key is not None:
                selected_keys[0] = on_only_key
            if near_only_key is not None:
                selected_keys[1] = near_only_key
            if off_only_key is not None:
                selected_keys[2] = off_only_key
            if min_power_key is not None:
                selected_keys[3] = min_power_key

            # Filter csv_data to only include rows matching selected_keys
            filtered_csv_data = []
            for selected_key in selected_keys:
                # find matching row in csv_data
                max_lifetime = 0
                max_row = None
                for row in csv_data:
                    if (row.get('implant_jobs'), row.get('near_jobs'), row.get('off_jobs')) == selected_key:
                        # compare lifetime and find the greatest one
                        if max_lifetime <= row.get('lifetime'):
                            max_lifetime = row.get('lifetime')
                            max_row = row

                filtered_csv_data.append(max_row)

            if filtered_csv_data:
                with open(csv_filename, 'w', newline='') as csvfile:
                    fieldnames = filtered_csv_data[0].keys()
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(filtered_csv_data)
                print(f'💾 Saved raw data to: {csv_filename} (filtered to 4 configs)')

    # Create Gantt chart for latency analysis only when optimizing for latency or implant_power
    if (optimize_metric == 'latency' or optimize_metric == 'implant_power') and is_latency_breakdown:
        # Create Gantt charts for each config_key
        for config_key, data_points in breakdown_data.items():
            if not data_points:
                continue
                
            comm_type, env_type, power_type = config_key
            
            # Filter data points based on offloading_map constraints
            filtered_points = []
            for point in data_points:
                # Check if point matches offloading_map constraints
                matches_constraints = True
                
                # Check num_electrode constraint
                if 'num_electrode' in offloading_map and point['num_elec'] != offloading_map['num_electrode']:
                    matches_constraints = False
                
                # Check off-implant constraint (extract from offloading_score)
                # offloading_score = implant_jobs + 10 * near_jobs + 100 * off_jobs
                # So off_jobs = offloading_score // 100, near_jobs = (offloading_score % 100) // 10
                if 'off' in offloading_map:
                    off_jobs = point['offloading_score'] // 100
                    if off_jobs != offloading_map['off']:
                        matches_constraints = False

                if matches_constraints:
                    filtered_points.append(point)
            
            if not filtered_points:
                print(f"⚠️ No data points match offloading_map constraints for {config_key}")
                continue
            
            # Find min and max latency configurations  
            latency_data = {}
            latency_csv_data = []
            
            for point in filtered_points:
                latency_breakdown = point['latency_breakdown']
                if latency_breakdown:
                    score = point['offloading_score']
                    off_jobs = score // 100
                    remaining = score % 100
                    near_jobs = remaining // 10
                    implant_jobs = remaining % 10
                    
                    offload_key = (implant_jobs, near_jobs, off_jobs)
                    # Use actual latency from simulation result, not calculated from breakdown
                    actual_latency = point['total_latency']
                    
                    if offload_key not in latency_data:
                        latency_data[offload_key] = {
                            'actual_latency': [],
                            'breakdown': latency_breakdown,
                            'num_elec': point['num_elec']
                        }
                    
                    latency_data[offload_key]['actual_latency'].append(actual_latency)
            
            # Average latency values for each configuration
            avg_latency_data = {}
            for offload_key, data in latency_data.items():
                avg_latency_data[offload_key] = {
                    'actual_latency': np.mean(data['actual_latency']),
                    'breakdown': data['breakdown'],
                    'num_elec': data['num_elec']
                }
            
            if not avg_latency_data:
                print(f"⚠️ No valid latency breakdown data for {config_key}")
            else:
                # Find max and min latency configurations based on actual simulation latency
                max_latency_key = max(avg_latency_data.keys(), key=lambda k: avg_latency_data[k]['actual_latency'])
                min_latency_key = min(avg_latency_data.keys(), key=lambda k: avg_latency_data[k]['actual_latency'])
                
                max_latency = avg_latency_data[max_latency_key]['actual_latency']
                min_latency = avg_latency_data[min_latency_key]['actual_latency']
                
                print(f"⏱️ Max latency: {max_latency:.3f} ms at I:{max_latency_key[0]} N:{max_latency_key[1]} O:{max_latency_key[2]}")
                print(f"⏱️ Min latency: {min_latency:.3f} ms at I:{min_latency_key[0]} N:{min_latency_key[1]} O:{min_latency_key[2]}")
                
                # Create Gantt chart for min and max latency configurations
                selected_latency_keys = [min_latency_key, max_latency_key]
                selected_latency_labels = ['Min Latency', 'Max Latency']
                
                # Define operation colors for Gantt chart
                operation_colors = {
                    'sensing': '#FF6B6B',
                    'computation': '#4ECDC4', 
                    'data_transmission': '#45B7D1',
                    'data_reception': '#96CEB4',
                    'power_transmission': '#DDA0DD',
                    'power_reception': '#98FB98',
                    'idle': '#D3D3D3'
                }
                
                # Create Gantt chart
                fig, axes = plt.subplots(2, 1, figsize=(14, 12))
                
                for config_idx, (latency_key, config_label) in enumerate(zip(selected_latency_keys, selected_latency_labels)):
                    ax = axes[config_idx]
                    breakdown = avg_latency_data[latency_key]['breakdown']
                    
                    print("Inside Gantt chart loop for config:", config_label, 
                          f" I:{latency_key[0]} N:{latency_key[1]} O:{latency_key[2]}")
                    node_types = ['on', 'near', 'off']
                    for node_type in node_types:
                        # print latency_components for debugging
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            print(f"Debug: {node_type} latency components: {breakdown[node_type]['latency_components']}")
                    
                    # Get the duration from breakdown for X-axis scaling
                    breakdown_duration = breakdown.get('duration', max(max_latency, min_latency))
                    
                    # Define all possible categories with their display names
                    all_categories = [
                        ('sensing', 'Sensor Operations'),
                        ('computation', 'Computing Operations'), 
                        ('data_transmission', 'Transmitting Data'),
                        ('data_reception', 'Receiving Data'),
                        ('power_transmission', 'Transmitting Power'),
                        ('power_reception', 'Receiving Power')
                    ]
                    
                    # Create Y-axis labels: one for each node-category combination (always show all)
                    y_labels = []
                    y_positions = []
                    y_pos = 0
                    
                    for node_type in ['on', 'near', 'off']:
                        node_name = f"{node_type.upper()} Node"
                        
                        # Group operations by category for this node (if they exist)
                        category_operations = {}
                        if node_type in breakdown and breakdown[node_type]['operations']:
                            operations = breakdown[node_type]['operations']
                            for op in operations:
                                if (op['category'] != 'idle' and 
                                    op['duration'] > 0 and 
                                    op['start_time'] != op['end_time']):
                                    category = op['category']
                                    if category not in category_operations:
                                        category_operations[category] = []
                                    category_operations[category].append(op)
                        
                        # Always create a row for each category (even if no operations)
                        for category_key, category_display in all_categories:
                            # Add label and position
                            y_labels.append(f"{node_name}\n{category_display}")
                            y_positions.append(y_pos)
                            
                            # Plot operations for this category if they exist
                            if category_key in category_operations:
                                for op in category_operations[category_key]:
                                    start_time = op['start_time']
                                    duration = op['duration']
                                    color = operation_colors.get(category_key, '#CCCCCC')
                                    
                                    ax.barh(y_pos, duration, left=start_time, height=0.8,
                                           color=color, alpha=0.8, edgecolor='black', linewidth=0.5)
                            
                            y_pos += 1
                        
                        # Add spacing between nodes
                        if node_type != 'off':  # Don't add spacing after the last node
                            y_pos += 0.5
                    
                    ax.set_yticks(y_positions)
                    ax.set_yticklabels(y_labels, fontsize=9)
                    ax.set_xlabel('Time (ms)')
                    ax.set_title(f'{config_label}: I:{latency_key[0]} N:{latency_key[1]} O:{latency_key[2]} '
                               f'(Total: {avg_latency_data[latency_key]["actual_latency"]:.2f} ms)')
                    ax.grid(True, alpha=0.3, axis='x')
                    ax.set_xlim(0, breakdown_duration * 1.05)  # Use duration from breakdown instead of total latency
                    
                    # Invert y-axis to show 'on' at top
                    ax.invert_yaxis()
                
                # Create legend with proper display names (exclude idle from legend)
                category_display_map = {
                    'sensing': 'Sensor Operations',
                    'computation': 'Computing Operations', 
                    'data_transmission': 'Transmitting Data',
                    'data_reception': 'Receiving Data',
                    'power_transmission': 'Transmitting Power',
                    'power_reception': 'Receiving Power'
                }
                legend_elements = [plt.Rectangle((0,0),1,1, facecolor=color, alpha=0.8, label=category_display_map.get(op_type, op_type)) 
                                 for op_type, color in operation_colors.items() if op_type != 'idle']
                fig.legend(handles=legend_elements, loc='center right', bbox_to_anchor=(1.15, 0.5))
                
                plt.suptitle(f'Latency Gantt Chart Analysis: {desired_application}\n{comm_type} - {env_type} - {power_type}', 
                           fontsize=14, fontweight='bold')
                
                # Save the Gantt chart
                gantt_filename = f'latency_gantt_{desired_application}_{comm_type}_{env_type}_{power_type}'
                plt.savefig(f'{result_dir}/{gantt_filename}.svg', dpi=50, bbox_inches='tight')
                plt.close()
                
                print(f'📊 Saved Gantt chart: {result_dir}/{gantt_filename}.svg')
                
                # Create latency breakdown plot based on latency_components
                # This shows the breakdown of latency using original operation keys for each node
                
                fig, axes = plt.subplots(2, 1, figsize=(16, 12))
                
                for config_idx, (latency_key, config_label) in enumerate(zip(selected_latency_keys, selected_latency_labels)):
                    ax = axes[config_idx]
                    breakdown = avg_latency_data[latency_key]['breakdown']
                    actual_latency = avg_latency_data[latency_key]['actual_latency']

                    # Collect all unique operation names across all nodes for consistent coloring
                    all_operations = set()
                    node_types = ['on', 'near', 'off']
                    node_labels = ['Implant Node', 'Near Node', 'Off Node']
                    
                    for node_type in node_types:
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            all_operations.update(breakdown[node_type]['latency_components'].keys())
                            
                    for node_type in node_types:
                        # print latency_components for debugging
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            print(f"Debug: {node_type} latency components: {breakdown[node_type]['latency_components']}")
                    
                    print(f"Debug: All operations found for {config_label}: {all_operations}")
                    
                    # Create color map for all operations (use more distinct colors)
                    operation_colors = {}
                    color_palette = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#DDA0DD', '#98FB98', 
                                   '#FFEAA7', '#FD79A8', '#FDCB6E', '#6C5CE7', '#A29BFE', '#FF7675',
                                   '#74B9FF', '#0984E3', '#00B894', '#00CEC9', '#E84393', '#FD79A8']
                    
                    for i, operation in enumerate(sorted(all_operations)):
                        operation_colors[operation] = color_palette[i % len(color_palette)]
                    
                    # Add idle color (always gray)
                    operation_colors['idle'] = '#D3D3D3'
                    
                    # Collect latency data for each node using original operation names
                    node_data = {}
                    
                    for node_type in node_types:
                        node_data[node_type] = {}
                        total_node_time = 0
                        
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            components = breakdown[node_type]['latency_components']
                            print(f"Debug: {node_type} components: {components}")
                            
                            # Use original operation names directly
                            for operation, duration in components.items():
                                node_data[node_type][operation] = duration
                                total_node_time += duration
                        
                        # Calculate idle time to make total equal to actual latency
                        if total_node_time < actual_latency:
                            idle_time = actual_latency - total_node_time
                            # check if node_data contains 'idle' key
                            if 'idle' not in node_data[node_type]:
                                node_data[node_type]['idle'] = idle_time
                            else:
                                node_data[node_type]['idle'] += idle_time
                            print(f"Debug: {node_type} idle time: {idle_time} idle time: {node_data[node_type]['idle']}")
                        else:
                            if 'idle' not in node_data[node_type]:
                                node_data[node_type]['idle'] = 0
                        
                        print(f"Debug: {node_type} total time: {total_node_time + node_data[node_type]['idle']}")
                    
                    # Create stacked bar chart
                    x_pos = range(len(node_types))
                    bottom_values = [0] * len(node_types)
                    
                    # Get all operations including idle for this configuration
                    all_operations_with_idle = set()
                    for node_type in node_types:
                        all_operations_with_idle.update(node_data[node_type].keys())
                    
                    # Sort operations for consistent ordering (idle last)
                    sorted_operations = sorted([op for op in all_operations_with_idle if op != 'idle'])
                    if 'idle' in all_operations_with_idle:
                        sorted_operations.append('idle')
                    
                    print(f"Debug: Sorted operations to plot: {sorted_operations}")
                    
                    # Plot each operation
                    plotted_operations = []  # Track what actually gets plotted
                    for operation in sorted_operations:
                        operation_values = []
                        for node_type in node_types:
                            value = node_data[node_type].get(operation, 0)
                            operation_values.append(value)
                        
                        print(f"Debug: Operation '{operation}' values: {operation_values}")
                        
                        # Only plot if there are non-zero values
                        if any(v > 0 for v in operation_values):
                            color = operation_colors.get(operation, '#CCCCCC')
                            
                            bars = ax.bar(x_pos, operation_values, bottom=bottom_values,
                                         label=operation, color=color, alpha=0.8, edgecolor='black', linewidth=0.5)
                            
                            plotted_operations.append(operation)
                            
                            # Add value labels on bars for significant components
                            for i, (bar, value) in enumerate(zip(bars, operation_values)):
                                if value > 0.01:  # Only show labels for values > 0.01 ms
                                    height = bar.get_height()
                                    y_pos = bottom_values[i] + value/2  # Center the label in the bar segment
                                    ax.text(bar.get_x() + bar.get_width()/2., y_pos,
                                           f'{value:.1f}', ha='center', va='center', 
                                           fontsize=8, fontweight='bold')
                            
                            # Update bottom values for stacking
                            bottom_values = [b + v for b, v in zip(bottom_values, operation_values)]
                    
                    print(f"Debug: Actually plotted operations: {plotted_operations}")
                    
                    # Verify that total height equals actual latency
                    for i, (node_type, node_label) in enumerate(zip(node_types, node_labels)):
                        total_time = sum(node_data[node_type].values())
                        ax.text(i, bottom_values[i] + max(bottom_values) * 0.02,
                               f'Total: {total_time:.1f} ms', ha='center', va='bottom',
                               fontsize=10, fontweight='bold',
                               bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.7))
                    
                    ax.set_title(f'{config_label}: I:{latency_key[0]} N:{latency_key[1]} O:{latency_key[2]} '
                               f'(Overall Latency: {actual_latency:.2f} ms)')
                    ax.set_xlabel('Node Type')
                    ax.set_ylabel('Latency (ms)')
                    ax.set_xticks(x_pos)
                    ax.set_xticklabels(node_labels)
                    ax.grid(True, alpha=0.3, axis='y')
                    
                    # Add legend only to the first subplot
                    if config_idx == 0 and plotted_operations:
                        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=9)
                
                plt.suptitle(f'Latency Breakdown Analysis: {desired_application}\n{comm_type} - {env_type} - {power_type}', 
                           fontsize=14, fontweight='bold')
                plt.tight_layout()
                
                # Save the latency breakdown plot
                breakdown_filename = f'latency_breakdown_{desired_application}_{comm_type}_{env_type}_{power_type}'
                plt.savefig(f'{result_dir}/{breakdown_filename}.svg', dpi=150, bbox_inches='tight')
                plt.close()
                
                print(f'📊 Saved latency breakdown plot: {result_dir}/{breakdown_filename}.svg')
                
                # Save latency breakdown raw data to CSV - include ALL configurations
                breakdown_csv_data = []
                
                # First pass: collect all unique operation names across ALL configurations
                all_operations = set()
                for latency_key in avg_latency_data.keys():  # Use all configurations, not just selected ones
                    breakdown = avg_latency_data[latency_key]['breakdown']
                    for node_type in ['on', 'near', 'off']:
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            all_operations.update(breakdown[node_type]['latency_components'].keys())
                all_operations.add('idle')  # Always include idle
                
                # Second pass: create CSV rows for ALL configurations ensuring all have the same fields
                # Identify the four requested configurations
                min_latency_key = None
                min_latency = float('inf')
                for latency_key, data in avg_latency_data.items():
                    if data['actual_latency'] < min_latency:
                        min_latency = data['actual_latency']
                        min_latency_key = latency_key

                # Find on-only, near-only, off-only configs
                on_only_key = None
                near_only_key = None
                off_only_key = None
                for latency_key in avg_latency_data.keys():
                    on, near, off = latency_key
                    if on > 0 and near == 0 and off == 0:
                        on_only_key = latency_key
                    if on == 0 and near > 0 and off == 0:
                        near_only_key = latency_key
                    if on == 0 and near == 0 and off > 0:
                        off_only_key = latency_key

                selected_keys = [0,0,0,0]
                if on_only_key is not None:
                    selected_keys[0] = on_only_key
                if near_only_key is not None:
                    selected_keys[1] = near_only_key
                if off_only_key is not None:
                    selected_keys[2] = off_only_key
                if min_latency_key is not None:
                    selected_keys[3] = min_latency_key

                for latency_key in selected_keys:
                    breakdown = avg_latency_data[latency_key]['breakdown']
                    actual_latency = avg_latency_data[latency_key]['actual_latency']
                    config_label = f"I:{latency_key[0]}_N:{latency_key[1]}_O:{latency_key[2]}"
                    for node_type in ['on', 'near', 'off']:
                        csv_row = {
                            'configuration': config_label,
                            'implant_jobs': latency_key[0],
                            'near_jobs': latency_key[1],
                            'off_jobs': latency_key[2],
                            'node_type': node_type,
                            'total_latency': actual_latency,
                            'compute_latency': breakdown.get('compute_latency', 0),
                            'communication_latency': breakdown.get('communication_latency', 0)
                        }
                        # Initialize all operation fields to 0
                        for operation in all_operations:
                            if operation != 'idle':
                                csv_row[operation] = 0
                        # Fill actual operation times
                        total_node_time = 0
                        if node_type in breakdown and 'latency_components' in breakdown[node_type]:
                            components = breakdown[node_type]['latency_components']
                            for operation, duration in components.items():
                                csv_row[operation] = duration
                                total_node_time += duration
                        # Calculate and add idle time
                        idle_time = max(0, actual_latency - total_node_time)
                        csv_row['idle'] = idle_time
                        breakdown_csv_data.append(csv_row)
                
                # Save breakdown CSV
                breakdown_csv_filename = f'{result_dir}/latency_breakdown_data_{desired_application}_{comm_type}_{env_type}_{power_type}.csv'
                if breakdown_csv_data:
                    with open(breakdown_csv_filename, 'w', newline='') as csvfile:
                        fieldnames = breakdown_csv_data[0].keys()
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(breakdown_csv_data)
                    print(f'💾 Saved latency breakdown data to: {breakdown_csv_filename}')

elif component_types == 'processor':
    print("Processing processor component type...")
    # Processor-specific code would go here if needed
    pass

else:
    print(f"Unknown component type: {component_types}")
