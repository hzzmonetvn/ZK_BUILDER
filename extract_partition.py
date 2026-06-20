import os
import sys
import zipfile
import subprocess
import argparse
import urllib.request

def find_file_in_zip(namelist, target_suffix):
    """Finds a file in zip namelist that ends with the target suffix."""
    for name in namelist:
        # Match exact name or name with path prefix
        if name == target_suffix or name.endswith('/' + target_suffix):
            return name
    return None

def download_lpunpack(tools_dir):
    """Downloads static lpunpack binary for linux x86_64 if not already present."""
    lpunpack_path = os.path.join(tools_dir, "lpunpack")
    if os.path.exists(lpunpack_path):
        return lpunpack_path
        
    url = "https://github.com/LonelyFool/lpunpack_and_lpmake/releases/download/v2.0/lpunpack"
    print(f"Downloading static lpunpack binary from {url}...")
    try:
        urllib.request.urlretrieve(url, lpunpack_path)
        os.chmod(lpunpack_path, 0o755)
        print(f"Successfully downloaded lpunpack to {lpunpack_path}")
        return lpunpack_path
    except Exception as e:
        print(f"Warning: Failed to download lpunpack: {e}")
        # Return fallback name in case it's in path
        return "lpunpack"

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
                # Fallback to python custom getsuper or standard payload tool in path
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
                # Clean temp dir
                os.rmdir(temp_out_dir)
                print("Extraction from payload.bin successful.")
                return
            else:
                print(f"Error: Failed to dump {partition_name} from payload.bin. Output:")
                print(res.stdout.decode('utf-8', errors='ignore'))
                print(res.stderr.decode('utf-8', errors='ignore'))
                # Cleanup temp directory if it exists
                if os.path.exists(temp_out_dir):
                    import shutil
                    shutil.rmtree(temp_out_dir)
                sys.exit(1)

        # 3. Case 3: super.img is present in the zip
        super_path = find_file_in_zip(namelist, "super.img")
        if super_path:
            print(f"Found super.img in zip: {super_path}")
            temp_super = "temp_super.img"
            print(f"Extracting {super_path} to {temp_super}...")
            with open(temp_super, "wb") as f_out:
                f_out.write(z.read(super_path))
                
            lpunpack_tool = download_lpunpack(tools_dir)
            
            print(f"Unpacking super.img for partition {partition_name}...")
            temp_out_dir = "temp_super_out"
            os.makedirs(temp_out_dir, exist_ok=True)
            
            cmd = [lpunpack_tool, "-p", partition_name, temp_super, temp_out_dir]
            print(f"Running command: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Clean up super.img immediately
            if os.path.exists(temp_super):
                os.remove(temp_super)
                
            dumped_img = os.path.join(temp_out_dir, f"{partition_name}.img")
            if os.path.exists(dumped_img):
                os.makedirs(os.path.dirname(output_img_path), exist_ok=True)
                os.rename(dumped_img, output_img_path)
                # Clean temp dir
                os.rmdir(temp_out_dir)
                print("Extraction from super.img successful.")
                return
            else:
                print(f"Error: Failed to unpack {partition_name} from super.img. Output:")
                print(res.stdout.decode('utf-8', errors='ignore'))
                print(res.stderr.decode('utf-8', errors='ignore'))
                if os.path.exists(temp_out_dir):
                    import shutil
                    shutil.rmtree(temp_out_dir)
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
