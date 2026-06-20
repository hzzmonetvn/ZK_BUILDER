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

def find_lpunpack(tools_dir):
    """Finds lpunpack binary. Checks tools_dir first, then clones repo if needed."""
    # Check in tools_dir
    lpunpack_path = os.path.join(tools_dir, "lpunpack")
    if os.path.exists(lpunpack_path):
        os.chmod(lpunpack_path, 0o755)
        return lpunpack_path

    # Check if available in system PATH
    result = subprocess.run(["which", "lpunpack"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        path = result.stdout.decode().strip()
        if path:
            return path

    # Clone Rprop/aosp15_partition_tools and use the linux binary
    clone_dir = "aosp15_partition_tools"
    if not os.path.exists(clone_dir):
        print("Cloning Rprop/aosp15_partition_tools for lpunpack binary...")
        res = subprocess.run(
            ["git", "clone", "--depth=1", "https://github.com/nicholaschum/super-image-dumper.git", clone_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if res.returncode != 0:
            # Try alternative repo
            res = subprocess.run(
                ["git", "clone", "--depth=1", "https://github.com/Rprop/aosp15_partition_tools.git", clone_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

    # Look for lpunpack in cloned repo
    for candidate in [
        os.path.join(clone_dir, "linux", "lpunpack"),
        os.path.join(clone_dir, "lpunpack"),
        os.path.join(clone_dir, "bin", "lpunpack"),
    ]:
        if os.path.exists(candidate):
            os.chmod(candidate, 0o755)
            # Copy to tools_dir for future use
            dest = os.path.join(tools_dir, "lpunpack")
            shutil.copy2(candidate, dest)
            os.chmod(dest, 0o755)
            # Clean up cloned repo
            shutil.rmtree(clone_dir, ignore_errors=True)
            return dest

    # Last resort: try to install via apt
    print("Attempting to install lpunpack via apt...")
    subprocess.run(["sudo", "apt-get", "install", "-y", "android-sdk-libsparse-utils"],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = subprocess.run(["which", "lpunpack"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        path = result.stdout.decode().strip()
        if path:
            return path

    print("Error: Could not find or install lpunpack.")
    sys.exit(1)

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
                
            # Check if super.img is sparse, convert to raw if needed
            with open(temp_super, "rb") as f:
                magic = f.read(4)
            if magic == b'\x3a\xff\x26\xed':
                print("Detected sparse super.img, converting to raw...")
                temp_raw = "temp_super_raw.img"
                res = subprocess.run(["simg2img", temp_super, temp_raw],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode == 0 and os.path.exists(temp_raw):
                    os.remove(temp_super)
                    os.rename(temp_raw, temp_super)
                    print("Converted sparse to raw successfully.")
                else:
                    print("Warning: simg2img conversion failed, trying with original file...")
                    if os.path.exists(temp_raw):
                        os.remove(temp_raw)

            lpunpack_tool = find_lpunpack(tools_dir)
            
            print(f"Unpacking super.img for partition {partition_name}...")
            temp_out_dir = "temp_super_out"
            os.makedirs(temp_out_dir, exist_ok=True)
            
            # Try with --slot=0 first (common for A/B devices)
            cmd = [lpunpack_tool, "--slot=0", "-p", f"{partition_name}_a", temp_super, temp_out_dir]
            print(f"Running command: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            dumped_img = os.path.join(temp_out_dir, f"{partition_name}_a.img")
            if not os.path.exists(dumped_img) or os.path.getsize(dumped_img) == 0:
                # Try without slot suffix
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                os.makedirs(temp_out_dir, exist_ok=True)
                cmd = [lpunpack_tool, "-p", partition_name, temp_super, temp_out_dir]
                print(f"Retrying command: {' '.join(cmd)}")
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                dumped_img = os.path.join(temp_out_dir, f"{partition_name}.img")
            
            # Clean up super.img immediately
            if os.path.exists(temp_super):
                os.remove(temp_super)
                
            if os.path.exists(dumped_img) and os.path.getsize(dumped_img) > 0:
                os.makedirs(os.path.dirname(output_img_path), exist_ok=True)
                os.rename(dumped_img, output_img_path)
                shutil.rmtree(temp_out_dir, ignore_errors=True)
                print("Extraction from super.img successful.")
                return
            else:
                print(f"Error: Failed to unpack {partition_name} from super.img. Output:")
                print(res.stdout.decode('utf-8', errors='ignore'))
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
