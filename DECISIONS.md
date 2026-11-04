# Decisions

## ROM comparison speed and partition coverage

- Chose to extract each ROM and unpack corresponding EROFS images concurrently; this targets the measured comparison-stage bottleneck while keeping per-partition disk cleanup.
- Made extractor temporary payload/super files and output directories unique per output image so the two concurrent extractor processes do not overwrite each other's work.
- Rejected HatchZip for ROM member extraction because its documented CLI extracts archives as a whole and does not select individual ZIP entries; ROM handling needs payload/super-aware extraction.
- Default partition behavior enumerates every logical partition present in either ROM's super metadata (including mi_ext, odm, and others), while preserving explicit user selection when supplied.
- The proxy ROM URL intermittently returns HTTP 500 during parallel range requests; retrying that download serially is preferable to treating a transient proxy failure as a missing ROM.
