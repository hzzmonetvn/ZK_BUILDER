"""Regression tests for ROM comparison metadata and extraction."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_partition as extract
import rom_compare_pipeline as pipeline
import compare_roms as compare


class PartitionDetectionTests(unittest.TestCase):
    def test_payload_dumper_go_format(self):
        output = ("payload.bin: payload.bin\nPayload Version: 2\nFound partitions:\n"
                  "boot (67 MB), product (3.4 GB), system (821 MB),\n"
                  "system_ext (288 MB), vendor_dlkm (28 MB)\nNumber of workers: 4\n")
        self.assertEqual(extract.parse_payload_partitions(output),
                         ["boot", "product", "system", "system_ext", "vendor_dlkm"])

    def test_no_silent_empty_partition_list(self):
        with self.assertRaises(ValueError):
            extract.parse_payload_partitions("Found partitions:\nNumber of workers: 4")

    def test_lpunpack_json_real_key(self):
        output = 'Read metadata\n{"partitions":[{"name":"system_a"},{"name":"vendor_a"}],"groups":[]}\n'
        self.assertEqual(extract.parse_super_partitions(output), ["system", "vendor"])

    def test_lpunpack_legacy_key(self):
        self.assertEqual(extract.parse_super_partitions('{"partition_table":[{"name":"product_b"}]}'),
                         ["product"])

    def test_select_super_over_payload_boot(self):
        d1 = {"source": "payload", "partitions": ["boot", "system", "vendor", "vendor_boot"]}
        d2 = {"source": "super", "partitions": ["system", "vendor", "product"]}
        self.assertEqual(extract.select_auto_partitions(d1, d2),
                         ["product", "system", "vendor"])

    def test_filter_payload_filesystems(self):
        d = {"source": "payload", "partitions": ["abl", "boot", "odm", "product", "vendor_dlkm"]}
        self.assertEqual(extract.select_auto_partitions(d, d),
                         ["odm", "product", "vendor_dlkm"])

    def test_reject_unsafe_partition(self):
        for value in ("../system", "system/a", "", "product;echo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract.valid_partition(value)

    def test_direct_zip_and_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            dest = Path(tmp) / "output" / "system.img"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("images/system_a.img", b"fake image payload")
            extract.extract_partition(rom, "system", dest, tmp)
            self.assertEqual(dest.read_bytes(), b"fake image payload")

    def test_detect_super_json_without_real_9gb_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("images/super.img", b"test")
            with mock.patch.object(extract, "run_tool", return_value='{"partitions":[{"name":"system_a"}]}'):
                found = extract.describe_partitions(rom, tmp)
            self.assertEqual(found, {"source": "super", "partitions": ["system"],
                                     "super_member": "images/super.img"})


    def test_renamed_super_img_recognized_by_lp_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            data = bytearray(8192)
            data[4096:4100] = extract.LP_GEOMETRY_MAGIC
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("images/P-nuwa-release.img", data)
                z.writestr("boot.img", b"boot")
            with mock.patch.object(extract, "run_tool",
                                   return_value='{"partitions":[{"name":"system_a"},{"name":"vendor_a"}]}'):
                result = extract.describe_partitions(rom, tmp)
            self.assertEqual(result["source"], "super")
            self.assertEqual(result["super_member"], "images/P-nuwa-release.img")
            self.assertEqual(result["partitions"], ["system", "vendor"])

    @unittest.skipUnless(shutil.which("zstd"), "requires the system zstd tool")
    def test_real_zstd_streaming_and_super_detection(self):
        # A real compressed ZIP member, not a mocked decompression result.
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            raw = bytearray(8192)
            raw[4096:4100] = extract.LP_GEOMETRY_MAGIC
            compressed = subprocess.run(["zstd", "-q", "-c"], input=bytes(raw),
                                        stdout=subprocess.PIPE, check=True).stdout
            with zipfile.ZipFile(rom, "w") as archive:
                archive.writestr("super.img.zst", compressed)
                archive.writestr("images/boot.img", b"boot")
            with mock.patch.object(extract, "run_tool",
                                   return_value='{"partitions":[{"name":"system_a"},{"name":"vendor_a"}]}'):
                data = extract.describe_partitions(rom, tmp)
            self.assertEqual(data["source"], "super")
            self.assertEqual(data["super_member"], "super.img.zst")
            self.assertEqual(data["partitions"], ["system", "vendor"])

    @unittest.skipUnless(shutil.which("zstd"), "requires the system zstd tool")
    def test_zstd_super_is_decompressed_for_partition_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            destination = Path(tmp) / "system.img"
            compressed = subprocess.run(["zstd", "-q", "-c"], input=b"superdata",
                                        stdout=subprocess.PIPE, check=True).stdout
            with zipfile.ZipFile(rom, "w") as archive:
                archive.writestr("super.img.zst", compressed)
            def fake_lpunpack(cmd):
                self.assertEqual(Path(cmd[-2]).read_bytes(), b"superdata")
                target = Path(cmd[-1])
                target.mkdir(exist_ok=True)
                (target / "system_a.img").write_bytes(b"extracted")
                return ""
            with mock.patch.object(extract, "run_tool", side_effect=fake_lpunpack):
                extract.extract_partition(rom, "system", destination, tmp,
                                          super_member="super.img.zst")
            self.assertEqual(destination.read_bytes(), b"extracted")

    @unittest.skipUnless(shutil.which("zstd"), "requires the system zstd tool")
    def test_cached_zstd_super_is_reused_without_decompression(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            cached = Path(tmp) / "cache"
            compressed = subprocess.run(["zstd", "-q", "-c"], input=b"superdata",
                                        stdout=subprocess.PIPE, check=True).stdout
            with zipfile.ZipFile(rom, "w") as archive:
                archive.writestr("super.img.zst", compressed)
            with mock.patch.object(extract, "run_tool",
                                   return_value='{"partitions":[{"name":"system_a"}]}'):
                metadata = extract.describe_partitions(rom, tmp, super_cache_dir=cached)
            self.assertEqual(Path(metadata["prepared_super_path"]).read_bytes(), b"superdata")
            destination = Path(tmp) / "system.img"
            def fake_lpunpack(cmd):
                self.assertEqual(Path(cmd[-2]).read_bytes(), b"superdata")
                output = Path(cmd[-1])
                (output / "system_a.img").write_bytes(b"from cached super")
                return ""
            with mock.patch.object(extract, "decompress_zstd_member") as decompress_again, \
                 mock.patch.object(extract, "run_tool", side_effect=fake_lpunpack):
                extract.extract_partition(rom, "system", destination, tmp,
                                          prepared_super_path=metadata["prepared_super_path"])
                decompress_again.assert_not_called()
            self.assertEqual(destination.read_bytes(), b"from cached super")

    def test_zstd_requires_system_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            with zipfile.ZipFile(rom, "w") as archive:
                archive.writestr("super.img.zst", b"not-really-zstd")
            with zipfile.ZipFile(rom) as archive, \
                 mock.patch.object(extract.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "zstd is required"):
                    extract.materialize_super(archive, [archive.getinfo("super.img.zst")],
                                              Path(tmp) / "super.img")

    def test_sparse_renamed_super_is_metadata_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("huge_unknown.img", extract.ANDROID_SPARSE_MAGIC + b"\0" * 100)
            def fake_materialize(archive, members, output):
                Path(output).write_bytes(b"verified sample")
            with mock.patch.object(extract, "SPARSE_IMAGE_BYTES", 64), \
                 mock.patch.object(extract, "materialize_super", side_effect=fake_materialize), \
                 mock.patch.object(extract, "run_tool",
                                   return_value='{"partitions":[{"name":"product_a"}]}'):
                result = extract.describe_partitions(rom, tmp)
            self.assertEqual(result["super_member"], "huge_unknown.img")
            self.assertEqual(result["partitions"], ["product"])

    def test_unreadable_large_super_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            data = bytearray(8192)
            data[4096:4100] = extract.LP_GEOMETRY_MAGIC
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("rom-giant.img", data)
                z.writestr("boot.img", b"boot")
            with mock.patch.object(extract, "run_tool", side_effect=RuntimeError("invalid metadata")):
                with self.assertRaisesRegex(RuntimeError, "none contained readable super"):
                    extract.describe_partitions(rom, tmp)

    def test_split_super_img_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("images/super.img.0", b"A" * 8192)
                z.writestr("images/super.img.1", b"B" * 8192)
                z.writestr("images/boot.img", b"boot")
            with mock.patch.object(extract, "run_tool",
                                   return_value='{"partitions":[{"name":"system_a"},{"name":"vendor_a"}]}'):
                found = extract.describe_partitions(rom, tmp)
            self.assertEqual(found["source"], "super")
            self.assertEqual(found["super_members"], ["images/super.img.0",
                                                        "images/super.img.1"])
            self.assertEqual(found["partitions"], ["system", "vendor"])

    def test_raw_split_super_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            target = Path(tmp) / "assembled.img"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("super.img.0", b"ABCD")
                z.writestr("super.img.1", b"EFGH")
            with zipfile.ZipFile(rom) as z:
                members = [z.getinfo("super.img.0"), z.getinfo("super.img.1")]
                extract.materialize_super(z, members, target)
            self.assertEqual(target.read_bytes(), b"ABCDEFGH")

    def test_split_super_member_used_for_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            dest = Path(tmp) / "system.img"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("super.img.0", b"AAAA")
                z.writestr("super.img.1", b"BBBB")
            def fake_unpack(cmd):
                output = Path(cmd[-1])
                output.mkdir(exist_ok=True)
                (output / "system_a.img").write_bytes(b"system contents")
                return ""
            with mock.patch.object(extract, "run_tool", side_effect=fake_unpack):
                extract.extract_partition(rom, "system", dest, tmp,
                                          super_members=["super.img.0", "super.img.1"])
            self.assertEqual(dest.read_bytes(), b"system contents")

    def test_selected_super_member_is_used_for_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            destination = Path(tmp) / "out" / "system.img"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("renamed-large.img", b"superdata")
            def unpack(cmd):
                output_dir = Path(cmd[-1])
                output_dir.mkdir(exist_ok=True)
                (output_dir / "system_a.img").write_bytes(b"partitiondata")
                return ""
            with mock.patch.object(extract, "run_tool", side_effect=unpack):
                extract.extract_partition(rom, "system", destination, tmp,
                                          super_member="renamed-large.img")
            self.assertEqual(destination.read_bytes(), b"partitiondata")

    def test_detect_zip_direct_partitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom.zip"
            with zipfile.ZipFile(rom, "w") as z:
                z.writestr("system_a.img", b"test")
            self.assertEqual(extract.describe_partitions(rom, tmp),
                             {"source": "images", "partitions": ["system"]})


class PipelineTests(unittest.TestCase):
    def test_image_format_ext4_and_erofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.img"
            b = bytearray(1100)
            b[1024:1028] = pipeline.EROFS_MAGIC
            p.write_bytes(b)
            self.assertEqual(pipeline.image_format(p), "erofs")
            b[1024:1028] = b"\x00" * 4
            b[1080:1082] = pipeline.EXT4_MAGIC
            p.write_bytes(b)
            self.assertEqual(pipeline.image_format(p), "ext4")
            b[:4] = pipeline.SPARSE_MAGIC
            p.write_bytes(b)
            self.assertEqual(pipeline.image_format(p), "sparse")

    def test_erofs_nested_roots_compare_matching_framework_jar(self):
        """Regression: different 'rom1'/'rom2' wrapper names must not create 0 modified."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rom1, rom2 = base / "rom1", base / "rom2"
            f1 = rom1 / "rom1" / "system" / "framework" / "framework.jar"
            f2 = rom2 / "rom2" / "system" / "framework" / "framework.jar"
            for dest in (f1, f2):
                dest.parent.mkdir(parents=True)
            # Same file size, different content, so hash comparison is required.
            f1.write_bytes(b"first ROM version")
            f2.write_bytes(b"second ROM versio")
            for root, label in ((rom1, "rom1"), (rom2, "rom2")):
                (root / "config").mkdir()
                (root / "config" / (label + "_file_contexts")).write_text("metadata")

            actual1 = pipeline.filesystem_root(rom1, "erofs")
            actual2 = pipeline.filesystem_root(rom2, "erofs")
            self.assertEqual(actual1, rom1 / "rom1")
            self.assertEqual(actual2, rom2 / "rom2")
            report = base / "report.md"
            compare.compare_partitions(actual1, actual2, "system", report)
            contents = report.read_text()
            self.assertIn("**Tệp bị thay đổi (Modified)**: 1", contents)
            self.assertIn("system/framework/framework.jar", contents)
            self.assertIn("Content changed", contents)
            self.assertIn("**Chỉ có ở ROM 1 (Xóa đi ở ROM 2)**: 0", contents)
            self.assertIn("**Chỉ có ở ROM 2 (Thêm mới ở ROM 2)**: 0", contents)

    def test_empty_or_disjoint_tree_never_produces_zero_modified_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            one, two = base / "one", base / "two"
            one.mkdir()
            two.mkdir()
            with self.assertRaisesRegex(ValueError, "Empty filesystem"):
                compare.compare_partitions(one, two, "system", base / "report.md")
            (one / "rom1").write_text("data")
            (two / "rom2").write_text("data")
            with self.assertRaisesRegex(ValueError, "No common relative file paths"):
                compare.compare_partitions(one, two, "system", base / "report.md")
            self.assertFalse((base / "report.md").exists())

    def test_hash_read_failure_is_not_considered_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                compare.get_file_hash_and_size(Path(tmp) / "missing.jar")

    def test_erofs_direct_layout_still_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "rom1"
            (root / "system").mkdir(parents=True)
            (root / "system" / "build.prop").write_text("build")
            self.assertEqual(pipeline.filesystem_root(root, "erofs"), root)

    def test_error_is_reflected_in_both_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "brief.md", Path(tmp) / "full.md"]
            pipeline.record(paths, "system", "**ERROR:** extraction failed")
            for p in paths:
                self.assertIn("extraction failed", p.read_text())


    def test_missing_partitions_are_skipped_without_fake_comparison(self):
        from argparse import Namespace
        from contextlib import redirect_stdout, redirect_stderr
        import io
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = Namespace(rom1=str(base / "rom1.zip"), rom2=str(base / "rom2.zip"),
                             tools=str(base), partitions="", report=str(base / "summary.md"),
                             full_report=str(base / "full.md"), diff_out=str(base / "diff"))
            rom1 = {"source": "payload", "partitions": ["system", "product"]}
            rom2 = {"source": "images", "partitions": ["cust", "boot"]}
            output = io.StringIO()
            with mock.patch.object(pipeline, "describe_partitions", side_effect=[rom1, rom2]), \
                 mock.patch.object(pipeline, "extract_partition") as extractor, \
                 redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(pipeline.run(args), 1)
            extractor.assert_not_called()
            self.assertIn("NO COMPARISON:", output.getvalue())
            self.assertIn("Đã bỏ qua **3**", (base / "summary.md").read_text())
            self.assertIn("Không có dữ liệu để so sánh", (base / "full.md").read_text())

    def test_only_shared_partition_is_processed(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = Namespace(rom1=str(base / "rom1.zip"), rom2=str(base / "rom2.zip"),
                             tools=str(base), partitions="", report=str(base / "summary.md"),
                             full_report=str(base / "full.md"), diff_out=str(base / "diff"))
            rom1 = {"source": "payload", "partitions": ["system", "vendor"]}
            rom2 = {"source": "images", "partitions": ["vendor", "cust"]}
            with mock.patch.object(pipeline, "describe_partitions", side_effect=[rom1, rom2]), \
                 mock.patch.object(pipeline, "extract_partition") as extractor, \
                 mock.patch.object(pipeline, "unpack_image", return_value="erofs"), \
                 mock.patch.object(pipeline, "filesystem_root", side_effect=lambda root, kind: root), \
                 mock.patch.object(pipeline, "command", return_value=("", "")) as compare_cmd:
                self.assertEqual(pipeline.run(args), 0)
            self.assertEqual(extractor.call_count, 2)
            self.assertEqual(compare_cmd.call_count, 1)
            self.assertIn("Đã bỏ qua **2**", (base / "full.md").read_text())


if __name__ == "__main__":
    unittest.main()
