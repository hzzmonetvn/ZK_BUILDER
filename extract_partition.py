"""Discover and extract Android ROM partitions from OTA or super ZIP files."""
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PARTITION_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LOGICAL_PARTITIONS = {
    "system", "system_ext", "system_other", "product", "vendor", "odm",
    "vendor_dlkm", "system_dlkm", "odm_dlkm", "mi_ext", "cust", "oem",
}


def normalise(name):
    return re.sub(r"_(a|b)$", "", name)


def is_logical(name):
    return name in LOGICAL_PARTITIONS or name.startswith("my_") or name.endswith("_dlkm")


def stream_extract(archive, entry, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    print("Extracting %s (%.2f GiB)..." % (entry.filename, entry.file_size / 1024**3), file=sys.stderr)
    with archive.open(entry) as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def find_entries(archive, partition_name=""):
    direct = payload = super_image = None
    direct_names = {name + ".img" for name in (partition_name, partition_name + "_a", partition_name + "_b")}
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        base = Path(entry.filename).name.lower()
        if partition_name and base in direct_names:
            direct = entry
        elif base == "payload.bin":
            payload = entry
        elif base.endswith(".img") and "super" in base:
            if super_image is None or entry.file_size > super_image.file_size:
                super_image = entry
    return direct, payload, super_image


def parse_payload_partitions(output):
    """Accept payload-dumper-go's comma-separated name (size) listing, including wraps."""
    marker = re.search(r"(?im)^[ \t]*Found partitions:[ \t]*", output)
    if not marker:
        raise ValueError("Payload listing did not contain 'Found partitions:'")
    body = output[marker.end():]
    body = re.split(r"(?im)^[ \t]*(?:Number of workers:|Extracting |Usage:)", body, maxsplit=1)[0]
    names = re.findall(r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]+\)", body)
    if not names:
        first = body.splitlines()[0].strip() if body.splitlines() else ""
        if re.fullmatch(r"[A-Za-z0-9_,\s]+", first):
            names = [item.strip() for item in first.split(",") if item.strip()]
    if not names:
        raise ValueError("Payload tool returned no parseable partition names: %s" % output[-1000:])
    return sorted({normalise(name) for name in names if PARTITION_RE.fullmatch(name)})


def parse_super_partitions(output):
    start = output.find("{")
    if start < 0:
        raise ValueError("lpunpack did not produce JSON: %s" % output[-1000:])
    metadata = json.loads(output[start:])
    # tools/py/lpunpack.py's ShowJsonInfo writes 'partitions', not 'partition_table'.
    entries = metadata.get("partitions", metadata.get("partition_table"))
    if not isinstance(entries, list):
        raise ValueError("Missing partitions array in lpunpack JSON")
    names = [entry["name"] for entry in entries if isinstance(entry, dict) and "name" in entry]
    if not names:
        raise ValueError("No logical partitions found in super metadata")
    return sorted({normalise(name) for name in names if PARTITION_RE.fullmatch(name)})


def run_tool(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError("%s exited %d:\n%s" % (" ".join(map(str, command)), result.returncode,
                                                   (result.stdout + "\n" + result.stderr)[-4000:]))
    return result.stdout + "\n" + result.stderr


def describe_partitions(zip_path, tools_dir):
    zip_path = Path(zip_path).resolve()
    tools_dir = Path(tools_dir).resolve()
    with zipfile.ZipFile(zip_path) as archive:
        _, payload, super_image = find_entries(archive)
        if payload is not None:
            tool = tools_dir / "payload"
            # Some payload-dumper-go builds read an OTA ZIP directly.
            try:
                names = parse_payload_partitions(run_tool([str(tool), "-l", str(zip_path)]))
            except (RuntimeError, ValueError) as direct_error:
                print("ZIP listing unavailable; falling back to payload.bin: %s" % direct_error,
                      file=sys.stderr)
                with tempfile.TemporaryDirectory(prefix="rom_payload_", dir=zip_path.parent) as tmp:
                    local = Path(tmp) / "payload.bin"
                    stream_extract(archive, payload, local)
                    names = parse_payload_partitions(run_tool([str(tool), "-l", str(local)]))
            return {"source": "payload", "partitions": names}
        if super_image is not None:
            with tempfile.TemporaryDirectory(prefix="rom_super_", dir=zip_path.parent) as tmp:
                local = Path(tmp) / "super.img"
                stream_extract(archive, super_image, local)
                unpacker = tools_dir / "py" / "lpunpack.py"
                output = run_tool([sys.executable, str(unpacker), "--info",
                                   "--format", "json", str(local)])
                return {"source": "super", "partitions": parse_super_partitions(output)}
        images = {normalise(Path(entry.filename).stem.lower()) for entry in archive.infolist()
                  if not entry.is_dir() and entry.filename.lower().endswith(".img")}
        if images:
            return {"source": "images", "partitions": sorted(images)}
    raise ValueError("No payload.bin, super.img, or partition images found inside %s" % zip_path)


def select_auto_partitions(rom1, rom2):
    """Use actual super metadata to avoid treating boot/modem as EROFS partitions."""
    super_sets = [set(d["partitions"]) for d in (rom1, rom2) if d["source"] == "super"]
    if super_sets:
        selected = set().union(*super_sets)
    else:
        selected = {name for d in (rom1, rom2) for name in d["partitions"] if is_logical(name)}
    if not selected:
        raise ValueError("No logical/filesystem partitions selected; specify partitions explicitly")
    return sorted(selected)


def valid_partition(name):
    if not PARTITION_RE.fullmatch(name):
        raise ValueError("Invalid partition name: %r" % name)
    return name


def extract_partition(zip_path, partition_name, output_img_path, tools_dir):
    partition_name = valid_partition(partition_name)
    zip_path = Path(zip_path).resolve()
    output_img_path = Path(output_img_path).resolve()
    tools_dir = Path(tools_dir).resolve()
    output_img_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        direct, payload, super_image = find_entries(archive, partition_name)
        if direct is not None:
            stream_extract(archive, direct, output_img_path)
            if output_img_path.stat().st_size == 0:
                raise RuntimeError("Empty direct partition image: %s" % direct.filename)
            return

        with tempfile.TemporaryDirectory(prefix="rom_extract_", dir=zip_path.parent) as tmp:
            tmp = Path(tmp)
            extracted = tmp / "images"
            extracted.mkdir()
            candidates = [partition_name, partition_name + "_a", partition_name + "_b"]
            failures = []

            if payload is not None:
                tool = str(tools_dir / "payload")
                # Read the archive directly when supported; otherwise unpack its payload.bin.
                payload_sources = [zip_path]
                for index, payload_source in enumerate(payload_sources):
                    for name in candidates:
                        try:
                            run_tool([tool, "-p", name, "-o", str(extracted), str(payload_source)])
                            result = extracted / (name + ".img")
                            if result.is_file() and result.stat().st_size:
                                shutil.move(str(result), str(output_img_path))
                                return
                        except RuntimeError as exc:
                            failures.append(str(exc))
                local_payload = tmp / "payload.bin"
                stream_extract(archive, payload, local_payload)
                for name in candidates:
                    try:
                        run_tool([tool, "-p", name, "-o", str(extracted), str(local_payload)])
                        result = extracted / (name + ".img")
                        if result.is_file() and result.stat().st_size:
                            shutil.move(str(result), str(output_img_path))
                            return
                    except RuntimeError as exc:
                        failures.append(str(exc))
                raise RuntimeError("Cannot extract %s from payload: %s" %
                                   (partition_name, "\n".join(failures)[-3000:]))

            if super_image is not None:
                local_super = tmp / "super.img"
                stream_extract(archive, super_image, local_super)
                unpacker = str(tools_dir / "py" / "lpunpack.py")
                for name in candidates:
                    try:
                        run_tool([sys.executable, unpacker, "-p", name,
                                  str(local_super), str(extracted)])
                        result = extracted / (name + ".img")
                        if result.is_file() and result.stat().st_size:
                            shutil.move(str(result), str(output_img_path))
                            return
                    except RuntimeError as exc:
                        failures.append(str(exc))
                raise RuntimeError("Cannot extract %s from super: %s" %
                                   (partition_name, "\n".join(failures)[-3000:]))
    raise ValueError("Partition %s not found; no suitable image, payload.bin, or super.img" %
                     partition_name)


def main():
    parser = argparse.ArgumentParser(description="Inspect and extract Android OTA/super partitions.")
    parser.add_argument("--zip", help="ROM ZIP")
    parser.add_argument("--partition", help="Partition name")
    parser.add_argument("--output", help="Output .img")
    parser.add_argument("--tools", help="Directory containing payload and lpunpack")
    parser.add_argument("--list-partitions", action="store_true", help="Print JSON array (legacy format)")
    parser.add_argument("--describe-partitions", action="store_true", help="Print JSON with source and names")
    parser.add_argument("--choose-partitions", nargs=2, metavar=("ROM1_JSON", "ROM2_JSON"))
    args = parser.parse_args()
    try:
        if args.choose_partitions:
            with open(args.choose_partitions[0], encoding="utf-8") as f1, \
                 open(args.choose_partitions[1], encoding="utf-8") as f2:
                print(json.dumps(select_auto_partitions(json.load(f1), json.load(f2))))
        elif args.describe_partitions or args.list_partitions:
            if not args.zip or not args.tools:
                parser.error("--zip and --tools are required for partition listing")
            data = describe_partitions(args.zip, args.tools)
            print(json.dumps(data if args.describe_partitions else data["partitions"]))
        else:
            if not all((args.zip, args.tools, args.partition, args.output)):
                parser.error("--zip, --tools, --partition, and --output are required")
            extract_partition(args.zip, args.partition, args.output, args.tools)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
