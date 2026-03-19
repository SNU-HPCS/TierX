import os

# integrity check
def clean_invalid_yaml_lines(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    cleaned_lines = []
    for line in lines:
        stripped = line.strip()

        # Skip if line is empty (keep it)
        if not stripped:
            cleaned_lines.append(line)
            continue

        # Keep line if it contains ':' or starts with a list item or starts with a "#"
        if ':' in stripped or stripped.startswith('- ') or stripped.startswith('#'):
            cleaned_lines.append(line)
        else:
            print(f"Invalid line detected and removed: '{stripped}' for file {file_path}")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(cleaned_lines)

# Example usage:
input_folder = 'lib/Input'
for filename in os.listdir(input_folder):
    if filename.endswith('.yaml'):
        file_path = os.path.join(input_folder, filename)
        clean_invalid_yaml_lines(file_path)
print("YAML file cleaned and written back successfully.")