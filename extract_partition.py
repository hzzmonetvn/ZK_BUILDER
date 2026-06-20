import os
import sys
import zipfile
import subprocess
import argparse
import shutil

def find_file_in_zip(namelist, target_suffix):
    """Finds a file in zip namelist that ends with the target suffix."""
    for name in namelist:
        if name == target_suffix or name.endswith('/' + target_suffix):
            return name
    return None

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
        namelist = z.namelist()
        
        # 1. Case 1: The target partition .img is directly inside the zip
        target_img = find_file_in_zip(namelist, f"{partition_name}.img")
        if target_img:
            print(f"Found direct partition image in zip: {target_img}")
            print(f"Extracting {target_img} to {output_img_path}...")
            os.makedirs(os.path.dirname(output_img_path), exist_ok=True)
            with open(output_img_path, "wb") as f_out:
                f_out.write(z.read(target_img))
            print("Extraction successful.")
            return

        # 2. Case 2: payload.bin is present in the zip
        payload_path = find_file_in_zip(namelist, "payload.bin")
        if payload_path:
            print(f"Found payload.bin in zip: {payload_path}")
            temp_payload = "temp_payload.bin"
            print(f"Extracting {payload_path} to {temp_payload}...")
            with open(temp_payload, "wb") as f_out:
                f_out.write(z.read(payload_path))
            
            print(f"Dumping {partition_name} from payload.bin using payload tool...")
            payload_tool = os.path.join(tools_dir, "payload")
            if not os.path.exists(payload_tool):
                payload_tool = "payload"
                
            temp_out_dir = "temp_payload_out"
            os.makedirs(temp_out_dir, exist_ok=True)
            
            cmd = [payload_tool, "-p", partition_name, "-o", temp_out_dir, temp_payload]
            print(f"Running command: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Clean up payload.bin immediately
            if os.path.exists(temp_payload):
                os.remove(temp_payload)
                
            dumped_img = os.path.join(temp_out_dir, f"{partition_name}.img")
            if os.path.exists(dumped_img):
                os.makedirs(os.path.dirname(output_img_path), exist_ok=True)
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

        # 3. Case 3: super.img is present in the zip
        super_path = find_file_in_zip(namelist, "super.img")
        if super_path:
            print(f"Found super.img in zip: {super_path}")
            temp_super = "temp_super.img"
            print(f"Extracting {super_path} to {temp_super}...")
            with open(temp_super, "wb") as f_out:
                f_out.write(z.read(super_path))

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
                os.makedirs(os.path.dirname(output_img_path), exist_ok=True)
                os.rename(dumped_img, output_img_path)
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                print(f"Extraction from super.img successful.")
                return
            else:
                print(f"Error: Failed to unpack {partition_name} from super.img. Output:")
                print(res.stderr.decode('utf-8', errors='ignore'))
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                sys.exit(1)

        print(f"Error: Could not find direct {partition_name}.img, payload.bin, or super.img in the zip file.")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trích xuất thông minh phân vùng từ ROM zip.")
    parser.add_argument("--zip", required=True, help="Đường dẫn file ROM zip")
    parser.add_argument("--partition", required=True, help="Tên phân vùng cần trích xuất")
    parser.add_argument("--output", required=True, help="Đường dẫn file .img đầu ra")
    parser.add_argument("--tools", required=True, help="Thư mục chứa tools")
    args = parser.parse_args()
    
    extract_partition(args.zip, args.partition, args.output, args.tools)
