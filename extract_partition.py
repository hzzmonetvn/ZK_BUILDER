import os
import sys
import zipfile
import subprocess
import argparse
import shutil

SIZE_8GB = int(7.5 * 1024 * 1024 * 1024)  # ~8GB threshold in bytes

def stream_extract(zip_file, member_info, target_path):
    """Streams file extraction from zip to target_path using chunks to prevent MemoryError."""
    print(f"Extracting {member_info.filename} ({member_info.file_size / (1024**3):.2f} GB) to {target_path}...")
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with zip_file.open(member_info, 'r') as src, open(target_path, 'wb') as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)

def find_entries(zip_file, partition_name):
    """Categorizes entries in zip file for partition extraction."""
    direct_img = None
    payload_entry = None
    super_entry = None

    for info in zip_file.infolist():
        fname = info.filename
        fname_lower = fname.lower()
        base_name = os.path.basename(fname_lower)

        # Match direct partition image (e.g. system.img) if not a huge super image
        if base_name == f"{partition_name.lower()}.img" and info.file_size < SIZE_8GB:
            direct_img = info

        # Match payload.bin
        elif base_name == "payload.bin":
            payload_entry = info

        # Match super.img or any xxx.img > 8GB
        elif fname_lower.endswith(".img") and (
            "super" in base_name or info.file_size >= SIZE_8GB
        ):
            if super_entry is None or info.file_size > super_entry.file_size:
                super_entry = info

    return direct_img, payload_entry, super_entry

def extract_partition(zip_path, partition_name, output_img_path, tools_dir):
    if not os.path.exists(zip_path):
        print(f"Error: Zip file not found at {zip_path}")
        sys.exit(1)
        
    print(f"Opening zip file {zip_path}...")
    try:
        z = zipfile.ZipFile(zip_path, 'r')
    except Exception as e:
        print(f"Error: Failed to open zip file {zip_path}: {e}")
        sys.exit(1)
        
    with z:
        direct_img, payload_entry, super_entry = find_entries(z, partition_name)

        # Case 1: Direct partition image found inside zip
        if direct_img:
            print(f"Found direct partition image in zip: {direct_img.filename}")
            stream_extract(z, direct_img, output_img_path)
            print("Direct extraction successful.")
            return

        # Case 2: payload.bin is present in zip
        if payload_entry:
            print(f"Found payload.bin in zip: {payload_entry.filename}")
            temp_payload = "temp_payload.bin"
            stream_extract(z, payload_entry, temp_payload)

            print(f"Dumping {partition_name} from payload.bin using payload tool...")
            payload_tool = os.path.join(tools_dir, "payload")
            if not os.path.exists(payload_tool):
                payload_tool = "payload"
                
            temp_out_dir = "temp_payload_out"
            os.makedirs(temp_out_dir, exist_ok=True)

            # Try dumping specific partition
            cmd = [payload_tool, "-p", partition_name, "-o", temp_out_dir, temp_payload]
            print(f"Running command: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            dumped_img = os.path.join(temp_out_dir, f"{partition_name}.img")
            
            # If specific partition dump fails, try with A/B suffix
            if not os.path.exists(dumped_img) or os.path.getsize(dumped_img) == 0:
                partition_ab = f"{partition_name}_a"
                cmd_ab = [payload_tool, "-p", partition_ab, "-o", temp_out_dir, temp_payload]
                print(f"Retrying with A/B suffix: {' '.join(cmd_ab)}")
                subprocess.run(cmd_ab, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                dumped_img_ab = os.path.join(temp_out_dir, f"{partition_ab}.img")
                if os.path.exists(dumped_img_ab) and os.path.getsize(dumped_img_ab) > 0:
                    dumped_img = dumped_img_ab

            # Clean up payload.bin immediately
            if os.path.exists(temp_payload):
                os.remove(temp_payload)

            if os.path.exists(dumped_img) and os.path.getsize(dumped_img) > 0:
                os.makedirs(os.path.dirname(os.path.abspath(output_img_path)), exist_ok=True)
                os.rename(dumped_img, output_img_path)
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                print("Extraction from payload.bin successful.")
                return
            else:
                print(f"Error: Failed to dump {partition_name} from payload.bin. Output:")
                print(res.stdout.decode('utf-8', errors='ignore'))
                print(res.stderr.decode('utf-8', errors='ignore'))
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                sys.exit(1)

        # Case 3: super.img or large .img (>= 8GB) present in zip
        if super_entry:
            print(f"Found super image in zip: {super_entry.filename} ({super_entry.file_size / (1024**3):.2f} GB)")
            temp_super = "temp_super.img"
            stream_extract(z, super_entry, temp_super)

            # Use lpunpack.py from tools/py/
            lpunpack_py = os.path.join(tools_dir, "py", "lpunpack.py")
            if not os.path.exists(lpunpack_py):
                print(f"Error: lpunpack.py not found at {lpunpack_py}")
                if os.path.exists(temp_super):
                    os.remove(temp_super)
                sys.exit(1)

            temp_out_dir = "temp_super_out"
            os.makedirs(temp_out_dir, exist_ok=True)

            # Try extracting with partition name directly
            cmd = ["python3", lpunpack_py, "-p", partition_name, temp_super, temp_out_dir]
            print(f"Running command: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(res.stdout.decode('utf-8', errors='ignore'))

            dumped_img = os.path.join(temp_out_dir, f"{partition_name}.img")

            # If not found, try with _a suffix (A/B devices)
            if not os.path.exists(dumped_img) or os.path.getsize(dumped_img) == 0:
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                os.makedirs(temp_out_dir, exist_ok=True)
                partition_ab = f"{partition_name}_a"
                cmd = ["python3", lpunpack_py, "-p", partition_ab, temp_super, temp_out_dir]
                print(f"Retrying with A/B suffix: {' '.join(cmd)}")
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print(res.stdout.decode('utf-8', errors='ignore'))
                dumped_img = os.path.join(temp_out_dir, f"{partition_ab}.img")

            # Clean up super.img immediately
            if os.path.exists(temp_super):
                os.remove(temp_super)

            if os.path.exists(dumped_img) and os.path.getsize(dumped_img) > 0:
                os.makedirs(os.path.dirname(os.path.abspath(output_img_path)), exist_ok=True)
                os.rename(dumped_img, output_img_path)
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                print(f"Extraction from super image successful.")
                return
            else:
                print(f"Error: Failed to unpack {partition_name} from super image. Output:")
                print(res.stderr.decode('utf-8', errors='ignore'))
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                sys.exit(1)

        print(f"Error: Could not find direct {partition_name}.img, payload.bin, or super image (>= 8GB) in zip file.")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trích xuất thông minh phân vùng từ ROM zip.")
    parser.add_argument("--zip", required=True, help="Đường dẫn file ROM zip")
    parser.add_argument("--partition", required=True, help="Tên phân vùng cần trích xuất")
    parser.add_argument("--output", required=True, help="Đường dẫn file .img đầu ra")
    parser.add_argument("--tools", required=True, help="Thư mục chứa tools")
    args = parser.parse_args()
    
    extract_partition(args.zip, args.partition, args.output, args.tools)
