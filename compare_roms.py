import os
import hashlib
import argparse

def get_file_hash_and_size(filepath):
    """Returns MD5 hash and size of a file."""
    hasher = hashlib.md5()
    try:
        size = os.path.getsize(filepath)
        # For huge files, read in chunks
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest(), size
    except Exception as e:
        return None, 0

def scan_directory(base_dir):
    """Scans directory recursively and returns a dict mapping relative paths to (size, hash)."""
    file_map = {}
    base_dir = os.path.abspath(base_dir)
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, base_dir)
            # Skip symbolic links for simplicity or handle them? 
            if os.path.islink(full_path):
                try:
                    target = os.readlink(full_path)
                    file_map[rel_path] = ("symlink", target)
                except Exception:
                    pass
            else:
                try:
                    size = os.path.getsize(full_path)
                    file_map[rel_path] = (size, None) # We delay hashing until needed to speed up
                except Exception:
                    pass
    return file_map

def compare_partitions(dir1, dir2, partition_name, output_md_path):
    print(f"Scanning {dir1}...")
    files1 = scan_directory(dir1)
    print(f"Scanning {dir2}...")
    files2 = scan_directory(dir2)

    only_in_1 = []
    only_in_2 = []
    modified = [] # items: (path, type_diff, detail)

    # All unique relative paths
    all_paths = sorted(list(set(files1.keys()) | set(files2.keys())))

    for rel_path in all_paths:
        in_1 = rel_path in files1
        in_2 = rel_path in files2

        if in_1 and not in_2:
            only_in_1.append(rel_path)
        elif in_2 and not in_1:
            only_in_2.append(rel_path)
        else:
            # Present in both
            val1 = files1[rel_path]
            val2 = files2[rel_path]

            # val is either ("symlink", target) or (size, None)
            if val1[0] == "symlink" or val2[0] == "symlink":
                if val1 != val2:
                    modified.append((rel_path, "Symlink changed", f"ROM1: {val1[1]} -> ROM2: {val2[1]}"))
            else:
                size1, _ = val1
                size2, _ = val2
                if size1 != size2:
                    modified.append((rel_path, "Size changed", f"Size: {size1} bytes -> {size2} bytes"))
                else:
                    # Same size, calculate MD5 hash to confirm
                    full_path1 = os.path.join(dir1, rel_path)
                    full_path2 = os.path.join(dir2, rel_path)
                    hash1, _ = get_file_hash_and_size(full_path1)
                    hash2, _ = get_file_hash_and_size(full_path2)
                    if hash1 != hash2:
                        modified.append((rel_path, "Content changed", f"MD5 mismatch"))

    # Write report
    with open(output_md_path, 'a', encoding='utf-8') as f:
        f.write(f"## Phân vùng / Partition: `{partition_name.upper()}`\n\n")
        f.write(f"- **Tổng số tệp trong ROM 1**: {len(files1)}\n")
        f.write(f"- **Tổng số tệp trong ROM 2**: {len(files2)}\n")
        f.write(f"- **Chỉ có ở ROM 1 (Xóa đi ở ROM 2)**: {len(only_in_1)}\n")
        f.write(f"- **Chỉ có ở ROM 2 (Thêm mới ở ROM 2)**: {len(only_in_2)}\n")
        f.write(f"- **Tệp bị thay đổi (Modified)**: {len(modified)}\n\n")

        limit = 100 # limit list display to avoid huge markdown file

        if only_in_1:
            f.write("<details>\n<summary><b>🔍 Danh sách tệp chỉ có ở ROM 1 (Xóa ở ROM 2) (Tối đa hiển thị 100)</b></summary>\n\n")
            f.write("| STT | Đường dẫn (Path) |\n|---|---|\n")
            for idx, path in enumerate(only_in_1[:limit]):
                f.write(f"| {idx+1} | `{path}` |\n")
            if len(only_in_1) > limit:
                f.write(f"| ... | Và {len(only_in_1) - limit} tệp khác... |\n")
            f.write("\n</details>\n\n")

        if only_in_2:
            f.write("<details>\n<summary><b>➕ Danh sách tệp chỉ có ở ROM 2 (Thêm mới ở ROM 2) (Tối đa hiển thị 100)</b></summary>\n\n")
            f.write("| STT | Đường dẫn (Path) |\n|---|---|\n")
            for idx, path in enumerate(only_in_2[:limit]):
                f.write(f"| {idx+1} | `{path}` |\n")
            if len(only_in_2) > limit:
                f.write(f"| ... | Và {len(only_in_2) - limit} tệp khác... |\n")
            f.write("\n</details>\n\n")

        if modified:
            f.write("<details>\n<summary><b>⚙️ Danh sách tệp bị sửa đổi (Modified) (Tối đa hiển thị 100)</b></summary>\n\n")
            f.write("| STT | Đường dẫn (Path) | Loại thay đổi | Chi tiết |\n|---|---|---|---|\n")
            for idx, (path, change_type, detail) in enumerate(modified[:limit]):
                f.write(f"| {idx+1} | `{path}` | {change_type} | {detail} |\n")
            if len(modified) > limit:
                f.write(f"| ... | Và {len(modified) - limit} tệp khác... | | |\n")
            f.write("\n</details>\n\n")
        
        f.write("---\n\n")

    print(f"Comparison for {partition_name} done. Report appended to {output_md_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="So sánh nhanh 2 thư mục phân vùng ROM.")
    parser.add_argument("--dir1", required=True, help="Thư mục ROM 1")
    parser.add_argument("--dir2", required=True, help="Thư mục ROM 2")
    parser.add_argument("--partition", required=True, help="Tên phân vùng")
    parser.add_argument("--output", required=True, help="Đường dẫn file markdown kết quả")
    args = parser.parse_args()

    compare_partitions(args.dir1, args.dir2, args.partition, args.output)
