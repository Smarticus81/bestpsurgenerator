"""Bounded ZIP intake. Only flat, regular source files may enter a run."""
import re
import stat
import zipfile
import zlib
from pathlib import Path

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 200
SUPPORTED_EXTENSIONS = {
    ".csv", ".xlsx", ".pdf", ".docx", ".json", ".txt", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _().-]{0,199}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


class UploadError(ValueError):
    pass


def extract_source_pack(archive_path: Path, input_dir: Path) -> None:
    """Validate metadata, then copy with actual decompression limits.

    The caller owns the isolated workspace and cleans it on any failure.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES:
                raise UploadError(f"ZIP must contain 1 to {MAX_FILES} files")
            seen = set()
            total = 0
            for entry in entries:
                name = entry.filename
                mode = entry.external_attr >> 16
                if (name != entry.orig_filename or not _SAFE_NAME.fullmatch(name)
                        or name.endswith((" ", "."))
                        or name.split(".")[0].rstrip().upper() in _RESERVED
                        or entry.is_dir()
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise UploadError("ZIP entries must be safe, flat, regular filenames")
                if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise UploadError(f"Unsupported source file type: {name}")
                if name.casefold() in seen:
                    raise UploadError(f"Duplicate filename: {name}")
                seen.add(name.casefold())
                if entry.flag_bits & 1:
                    raise UploadError("Encrypted ZIP files are not supported")
                if entry.file_size == 0 or entry.file_size > MAX_FILE_BYTES:
                    raise UploadError(f"Source file is empty or exceeds the size limit: {name}")
                total += entry.file_size
                if total > MAX_TOTAL_BYTES:
                    raise UploadError("Expanded ZIP exceeds the total size limit")
            input_dir.mkdir(parents=True, exist_ok=False)
            total = 0
            for entry in entries:
                size = 0
                with archive.open(entry) as source, (input_dir / entry.filename).open("xb") as target:
                    while chunk := source.read(64 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                            raise UploadError("Expanded ZIP exceeds the size limit")
                        target.write(chunk)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise UploadError("Invalid or unsupported ZIP archive") from exc
