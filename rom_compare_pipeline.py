"""Compare filesystem partitions from two Android ROM ZIP files."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from extract_partition import describe_partitions, extract_partition, select_auto_partitions, valid_partition

EXT4_MAGIC = b"\x53\xef"
EROFS_MAGIC = b"\xe2\xe1\xf5\xe0"
SPARSE_MAGIC = b"\x3a\xff\x26\xed"


def record(report_paths, part, message):
    for report in report_paths:
        with open(report, "a", encoding="utf-8") as f:
            f.write("## Phân vùng / Partition: " + chr(96) + part.upper() + chr(96) + "\n\n")
            f.write(message + "\n\n---\n\n")


def command(argv, context):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError("%s failed (%d):\n%s" %
                           (context, result.returncode, (result.stdout + result.stderr)[-4000:]))
    return result.stdout, result.stderr


def image_format(image):
    with open(image, "rb") as f:
        head = f.read(4)
        f.seek(1024)
        fs_head = f.read(64)
    if head == SPARSE_MAGIC:
        return "sparse"
    if fs_head[:4] == EROFS_MAGIC:
        return "erofs"
    if fs_head[56:58] == EXT4_MAGIC:
        return "ext4"
    return "unsupported"


def unpack_image(image, target_dir, erofs_tool):
    image = Path(image)
    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    kind = image_format(image)
    if kind == "sparse":
        if not shutil.which("simg2img"):
            raise RuntimeError("Sparse Android image requires simg2img (android-sdk-libsparse-utils)")
        raw_image = image.with_suffix(".raw.img")
        command(["simg2img", str(image), str(raw_image)], "Convert Android sparse image")
        image.unlink()
        image = raw_image
        kind = image_format(image)
    if kind == "erofs":
        command([str(erofs_tool), "-x", "-i", str(image), "-o", str(target_dir)],
                "EROFS extraction")
    elif kind == "ext4":
        # debugfs reads the image without loop devices or root filesystem mounts.
        if not shutil.which("debugfs"):
            raise RuntimeError("EXT4 extraction requires debugfs (e2fsprogs)")
        command(["debugfs", "-R", "rdump / " + str(target_dir), str(image)],
                "EXT4 extraction")
    else:
        raise RuntimeError("Unsupported filesystem image for %s" % image.name)
    if not any(target_dir.iterdir()):
        raise RuntimeError("Unpacked %s but extracted directory is empty" % image)
    return kind


def run(args):
    tools = Path(args.tools).resolve()
    zip1, zip2 = Path(args.rom1).resolve(), Path(args.rom2).resolve()
    reports = [Path(args.report).resolve(), Path(args.full_report).resolve()]
    for report in reports:
        if not report.is_file():
            report.write_text("# ROM Comparison Report\n\n", encoding="utf-8")
    d1 = describe_partitions(zip1, tools)
    d2 = describe_partitions(zip2, tools)
    print("ROM 1 partition source=%s: %s" % (d1["source"], ",".join(d1["partitions"])), flush=True)
    print("ROM 2 partition source=%s: %s" % (d2["source"], ",".join(d2["partitions"])), flush=True)
    if args.partitions.strip():
        requested = sorted(set(valid_partition(part.strip())
                               for part in args.partitions.split(",") if part.strip()))
        if not requested:
            raise ValueError("No partition names supplied")
    else:
        requested = select_auto_partitions(d1, d2)

    # Missing partitions are expected when comparing OTA payloads and modded ROMs.
    # Compare the intersection only; never describe an unexamined partition as compared.
    available1, available2 = set(d1["partitions"]), set(d2["partitions"])
    parts = [name for name in requested if name in available1 and name in available2]
    skipped = [name for name in requested if name not in available1 or name not in available2]
    print("Shared partitions to compare: " + (", ".join(parts) if parts else "(none)"), flush=True)
    if skipped:
        print("SKIPPED (%d): not present in both ROMs: %s" %
              (len(skipped), ", ".join(skipped)), flush=True)
        # Keep a concise audit trail instead of a separate empty section per partition.
        for report in reports:
            with open(report, "a", encoding="utf-8") as f:
                f.write("## Bỏ qua phân vùng không có trong cả hai ROM\n\n")
                f.write("Đã bỏ qua **%d** phân vùng không tồn tại đồng thời trong hai ROM.\n\n" % len(skipped))
                f.write("Danh sách: `%s`\n\n---\n\n" % ", ".join(skipped))
    Path(args.diff_out).mkdir(parents=True, exist_ok=True)
    failures = []
    compared = 0
    for part in parts:
        print(">>> Comparing partition: %s" % part, flush=True)
        try:
            # Extract one ROM at a time to avoid retaining two large raw super images.
            with tempfile.TemporaryDirectory(prefix="compare_" + part + "_", dir=Path.cwd()) as workspace:
                workspace = Path(workspace)
                out = []
                for idx, rom in enumerate((zip1, zip2), 1):
                    image = workspace / ("rom%d.img" % idx)
                    destination = workspace / ("rom%d" % idx)
                    print("Extracting ROM %d: %s" % (idx, part), flush=True)
                    metadata = d1 if idx == 1 else d2
                    extract_partition(rom, part, image, tools,
                                      super_member=metadata.get("super_member"),
                                      super_members=metadata.get("super_members"))
                    kind = unpack_image(image, destination, tools / "extract.erofs")
                    print("ROM %d: %s extracted as %s" % (idx, part, kind), flush=True)
                    image.unlink(missing_ok=True)
                    image.with_suffix(".raw.img").unlink(missing_ok=True)
                    out.append(destination)
                command([sys.executable, str(tools.parent / "compare_roms.py"),
                         "--dir1", str(out[0]), "--dir2", str(out[1]),
                         "--partition", part, "--output", str(reports[0]),
                         "--output-full", str(reports[1]), "--diff-out", str(Path(args.diff_out).resolve())],
                        "Compare partition " + part)
                compared += 1
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            error = "%s: %s" % (part, e)
            failures.append(error)
            record(reports, part, "**Extraction/comparison failed:** " + str(e).replace("\n", "  \n"))
            print("ERROR: " + error, file=sys.stderr, flush=True)
        finally:
            print(">>> Completed partition: " + part, flush=True)
    if failures:
        print("%d partition(s) failed:\n%s" % (len(failures), "\n".join(failures)),
              file=sys.stderr, flush=True)
        return 1
    if not compared:
        message = ("No shared filesystem partitions were compared. The ROM input "
                   "may be missing super.img, may use an unrecognized archive layout, "
                   "or may not contain any comparable filesystem partition.")
        print("NO COMPARISON: " + message, file=sys.stderr, flush=True)
        for report in reports:
            with open(report, "a", encoding="utf-8") as f:
                f.write("## Không có dữ liệu để so sánh\n\n" + message + "\n\n")
        return 1
    print("Compared %d partition(s) successfully; skipped %d missing." %
          (compared, len(skipped)), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Compare Android filesystem partitions safely")
    parser.add_argument("--rom1", required=True)
    parser.add_argument("--rom2", required=True)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--partitions", default="")
    parser.add_argument("--report", default="compare_report.md")
    parser.add_argument("--full-report", default="compare_report_full.md")
    parser.add_argument("--diff-out", default="rom_diff_out")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except (OSError, ValueError, RuntimeError) as e:
        print("FATAL: %s" % e, file=sys.stderr)
        for filename in (args.report, args.full_report):
            with open(filename, "a", encoding="utf-8") as f:
                f.write("\n**Comparison failed:** %s\n" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
