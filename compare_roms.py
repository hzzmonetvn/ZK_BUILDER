import os
import hashlib
import argparse
import shutil

def get_file_hash_and_size(filepath):
    """Returns MD5 hash and size of a file."""
    hasher = hashlib.md5()
    try:
        size = os.path.getsize(filepath)
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
            if os.path.islink(full_path):
                try:
                    target = os.readlink(full_path)
                    file_map[rel_path] = ("symlink", target)
                except Exception:
                    pass
            else:
                try:
                    size = os.path.getsize(full_path)
                    file_map[rel_path] = (size, None)
                except Exception:
                    pass
    return file_map

def safe_copy(src, dst):
    """Safely copies a file. If it's a symlink, creates a text file describing the link target."""
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            target = os.readlink(src)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(f"[SYMLINK TARGET]: {target}")
        else:
            shutil.copy2(src, dst)
    except Exception as e:
        print(f"Warning: Failed to copy {src} to {dst}: {e}")

def compare_partitions(dir1, dir2, partition_name, output_md_path, diff_out_dir=None):
    print(f"Scanning {dir1}...")
    files1 = scan_directory(dir1)
    print(f"Scanning {dir2}...")
    files2 = scan_directory(dir2)

    only_in_1 = []
    only_in_2 = []
    modified = []

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

            if val1[0] == "symlink" or val2[0] == "symlink":
                if val1 != val2:
                    modified.append((rel_path, "Symlink changed", f"ROM1: {val1[1]} -> ROM2: {val2[1]}"))
            else:
                size1, _ = val1
                size2, _ = val2
                if size1 != size2:
                    modified.append((rel_path, "Size changed", f"Size: {size1} bytes -> {size2} bytes"))
                else:
                    full_path1 = os.path.join(dir1, rel_path)
                    full_path2 = os.path.join(dir2, rel_path)
                    hash1, _ = get_file_hash_and_size(full_path1)
                    hash2, _ = get_file_hash_and_size(full_path2)
                    if hash1 != hash2:
                        modified.append((rel_path, "Content changed", f"MD5 mismatch"))

    # Copy diff files if diff_out_dir is provided
    if diff_out_dir:
        dir_a = os.path.join(diff_out_dir, f"{partition_name}_a")
        dir_b = os.path.join(diff_out_dir, f"{partition_name}_b")
        
        print(f"Copying different files to {diff_out_dir}...")
        for path in only_in_1:
            safe_copy(os.path.join(dir1, path), os.path.join(dir_a, path))
        for path in only_in_2:
            safe_copy(os.path.join(dir2, path), os.path.join(dir_b, path))
        for path, _, _ in modified:
            safe_copy(os.path.join(dir1, path), os.path.join(dir_a, path))
            safe_copy(os.path.join(dir2, path), os.path.join(dir_b, path))

    # Write report
    def write_report_file(file_path, limit):
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(f"## Phân vùng / Partition: `{partition_name.upper()}`\n\n")
            f.write(f"- **Tổng số tệp trong ROM 1**: {len(files1)}\n")
            f.write(f"- **Tổng số tệp trong ROM 2**: {len(files2)}\n")
            f.write(f"- **Chỉ có ở ROM 1 (Xóa đi ở ROM 2)**: {len(only_in_1)}\n")
            f.write(f"- **Chỉ có ở ROM 2 (Thêm mới ở ROM 2)**: {len(only_in_2)}\n")
            f.write(f"- **Tệp bị thay đổi (Modified)**: {len(modified)}\n\n")

            limit_str = f" (Tối đa hiển thị {limit})" if limit is not None else ""

            if only_in_1:
                f.write(f"<details>\n<summary><b>🔍 Danh sách tệp chỉ có ở ROM 1 (Xóa ở ROM 2){limit_str}</b></summary>\n\n")
                f.write("| STT | Đường dẫn (Path) |\n|---|---|\n")
                items = only_in_1[:limit] if limit is not None else only_in_1
                for idx, path in enumerate(items):
                    f.write(f"| {idx+1} | `{path}` |\n")
                if limit is not None and len(only_in_1) > limit:
                    f.write(f"| ... | Và {len(only_in_1) - limit} tệp khác... |\n")
                f.write("\n</details>\n\n")

            if only_in_2:
                f.write(f"<details>\n<summary><b>➕ Danh sách tệp chỉ có ở ROM 2 (Thêm mới ở ROM 2){limit_str}</b></summary>\n\n")
                f.write("| STT | Đường dẫn (Path) |\n|---|---|\n")
                items = only_in_2[:limit] if limit is not None else only_in_2
                for idx, path in enumerate(items):
                    f.write(f"| {idx+1} | `{path}` |\n")
                if limit is not None and len(only_in_2) > limit:
                    f.write(f"| ... | Và {len(only_in_2) - limit} tệp khác... |\n")
                f.write("\n</details>\n\n")

            if modified:
                f.write(f"<details>\n<summary><b>⚙️ Danh sách tệp bị sửa đổi (Modified){limit_str}</b></summary>\n\n")
                f.write("| STT | Đường dẫn (Path) | Loại thay đổi | Chi tiết |\n|---|---|---|---|\n")
                items = modified[:limit] if limit is not None else modified
                for idx, (path, change_type, detail) in enumerate(items):
                    f.write(f"| {idx+1} | `{path}` | {change_type} | {detail} |\n")
                if limit is not None and len(modified) > limit:
                    f.write(f"| ... | Và {len(modified) - limit} tệp khác... | | |\n")
                f.write("\n</details>\n\n")
            
            f.write("---\n\n")

    write_report_file(output_md_path, limit=100)
    if output_full_md_path:
        write_report_file(output_full_md_path, limit=None)

    print(f"Comparison for {partition_name} done. Report appended to {output_md_path}")
    if output_full_md_path:
        print(f"Full report appended to {output_full_md_path}")

def compare_partitions_main(dir1, dir2, partition_name, output_md_path, diff_out_dir=None, output_full_md_path=None):
    compare_partitions(dir1, dir2, partition_name, output_md_path, diff_out_dir, output_full_md_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="So sánh nhanh 2 thư mục phân vùng ROM.")
    parser.add_argument("--dir1", required=True, help="Thư mục ROM 1")
    parser.add_argument("--dir2", required=True, help="Thư mục ROM 2")
    parser.add_argument("--partition", required=True, help="Tên phân vùng")
    parser.add_argument("--output", required=True, help="Đường dẫn file markdown kết quả")
    parser.add_argument("--output-full", required=False, default=None, help="Đường dẫn file markdown kết quả đầy đủ (không giới hạn số tệp)")
    parser.add_argument("--diff-out", required=False, default=None, help="Thư mục lưu các file khác biệt để đóng gói")
    args = parser.parse_args()

    compare_partitions(args.dir1, args.dir2, args.partition, args.output, args.diff_out, args.output_full)
