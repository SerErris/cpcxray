"""Session methods for disk drives and images (design.md §6.11, §6.16; FR-FDC-01..05, phase 10).

A mixin of `Session`; every public method is an MCP tool as well (D-27):

* drives: `disk_list`, `disk_insert`, `disk_eject`;
* images: `disk_new` (a blank, writable draft in a drive), `disk_save` (EDSK to a file);
* host-side reading of the CP/M file system (disk/cpm.py): `disk_catalog`, `disk_read_file`,
  from the disk in a drive or from an image file - the reference for what the disk ROMs read.

Real images are inserted read-only unless asked otherwise; the emulated FDC then reports them
write protected, and `disk_save` refuses to overwrite the file a read-only image came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cpcxray.disk.cpm import CpmDisk, CpmError, amsdos_header
from cpcxray.disk.dsk import (
    BLANK_FORMATS,
    DiskImage,
    DiskImageError,
    blank_image,
    load_image,
    save_image,
)
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from pathlib import Path

    from cpcxray.machine.cpc464 import CPC464

MAX_FILE_PREVIEW = 4096


class DiskMixin:
    """Drive and disk image methods of `Session` (see the module docstring)."""

    # -- helpers (provided by Session) -----------------------------------------------------------

    def _path(self, path: str) -> Path:
        """Resolve a project-relative path (implemented by Session)."""
        raise NotImplementedError

    def _cpc(self) -> CPC464:
        """Return the machine as a CPC 464 (implemented by Session)."""
        raise NotImplementedError

    @staticmethod
    def _drive_letter(drive: str) -> str:
        """Normalise "a", "A:", "B" → "A" / "B"."""
        letter = drive.strip().rstrip(":").upper()
        if letter not in ("A", "B"):
            raise SessionError("drive must be A or B")
        return letter

    def _image_in(self, drive: str) -> DiskImage:
        """The image in a drive."""
        letter = self._drive_letter(drive)
        image = self._cpc().fdc.drives["AB".index(letter)].image
        if image is None:
            raise SessionError(f"no disk in drive {letter}")
        return image

    def _cpm(self, drive: str | None, path: str | None) -> CpmDisk:
        """The CP/M file system of a drive's disk or of an image file."""
        try:
            if path is not None:
                return CpmDisk(load_image(self._path(path)))
            return CpmDisk(self._image_in(drive or "A"))
        except (OSError, DiskImageError, CpmError) as error:
            raise SessionError(str(error)) from None

    # == drives ================================================================================

    def disk_list(self) -> dict[str, Any]:
        """Show both drives: inserted image, write permission, unsaved changes, head position."""
        drives = []
        for drive in self._cpc().fdc.drives:
            image = drive.image
            entry: dict[str, Any] = {"drive": drive.name, "cylinders": drive.cylinders,
                                     "double_sided": drive.double_sided,
                                     "head_cylinder": drive.cylinder, "disk": None}  # fmt: skip
            if image is not None:
                entry |= {"disk": str(image.source) if image.source else "draft",
                          "writable": image.writable, "modified": image.modified,
                          "image_cylinders": image.cylinders,
                          "image_heads": image.heads}  # fmt: skip
            drives.append(entry)
        return {"drives": drives}

    def disk_insert(self, drive: str, path: str, writable: bool = False) -> dict[str, Any]:
        """Insert a DSK, EDSK or raw Vortex image into drive A or B.

        Args:
            drive: "A" or "B".
            path: The image file.
            writable: Let the CPC write to it (in memory only; `disk_save` writes a file).
                Default read-only: the FDC reports the disk write protected.
        """
        letter = self._drive_letter(drive)
        try:
            image = load_image(self._path(path), writable)
        except (OSError, DiskImageError) as error:
            raise SessionError(str(error)) from None
        self._cpc().insert_disk(letter, image)
        return self.disk_list()

    def disk_eject(self, drive: str) -> dict[str, Any]:
        """Remove the disk from a drive (unsaved changes of a draft are lost)."""
        self._cpc().insert_disk(self._drive_letter(drive), None)
        return self.disk_list()

    # == images ================================================================================

    def disk_new(self, drive: str, disk_format: str = "vortex") -> dict[str, Any]:
        """Insert a blank, formatted, writable draft image ("vortex", "data" or "system").

        Use it for everything that writes; save it with `disk_save`.
        """
        if disk_format not in BLANK_FORMATS:
            raise SessionError(f"format must be one of {', '.join(BLANK_FORMATS)}")
        self._cpc().insert_disk(self._drive_letter(drive), blank_image(disk_format))
        return self.disk_list()

    def disk_save(self, drive: str, path: str | None = None) -> dict[str, Any]:
        """Save the disk in a drive as EDSK.

        Args:
            drive: "A" or "B".
            path: Target file. None saves a writable image back over the file it was loaded
                from; a read-only image is never overwritten, a draft needs a path.
        """
        image = self._image_in(drive)
        if path is None:
            if image.source is None:
                raise SessionError("the disk is a draft: give a file name (Save as)")
            if not image.writable:
                raise SessionError(f"{image.source} is read-only; save it under another name")
            file = image.source
        elif not path.strip():
            raise SessionError("give a file name to save the disk to, e.g. disk/my_disk.dsk")
        else:
            file = self._path(path.strip())
        if file.is_dir():
            raise SessionError(f"{file} is a directory; give a file name")
        try:
            size = save_image(image, file)
        except (DiskImageError, OSError) as error:
            raise SessionError(str(error)) from None
        return {"drive": self._drive_letter(drive), "path": str(file), "size": size}

    # == host-side file system =================================================================

    def disk_catalog(
        self, drive: str | None = "A", path: str | None = None, include_system: bool = True
    ) -> dict[str, Any]:
        """List the CP/M directory of a drive's disk (or of an image file), read directly.

        Args:
            drive: "A" or "B" (ignored when `path` is given).
            path: An image file instead of a drive.
            include_system: Also list files with the SYS attribute (CAT hides them).
        """
        disk = self._cpm(drive, path)
        try:
            files = disk.files()
        except CpmError as error:
            raise SessionError(str(error)) from None
        entries = [
            {"user": f.user, "name": f.name, "size": f.size, "read_only": f.read_only,
             "system": f.system}
            for f in files
            if include_system or not f.system
        ]  # fmt: skip
        used_blocks = sum(len(f.blocks) for f in files)
        return {"format": disk.format.name, "files": entries,
                "used_kb": used_blocks * disk.format.block_size // 1024}  # fmt: skip

    def disk_read_file(
        self, name: str, drive: str | None = "A", path: str | None = None, user: int = 0,
        out_path: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Read a file from a disk directly (no emulation): AMSDOS header and contents.

        Args:
            name: "NAME.EXT".
            drive: "A" or "B" (ignored when `path` is given).
            path: An image file instead of a drive.
            user: CP/M user number.
            out_path: Write the file there; otherwise up to 4 KB are returned (text and hex).
        """
        disk = self._cpm(drive, path)
        try:
            data = disk.read_file(name, user)
        except CpmError as error:
            raise SessionError(str(error)) from None
        result: dict[str, Any] = {"name": name.upper(), "size": len(data),
                                  "amsdos_header": amsdos_header(data)}  # fmt: skip
        if out_path is not None:
            file = self._path(out_path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            result["path"] = str(file)
            return result
        preview = data[:MAX_FILE_PREVIEW]
        result["hex"] = preview.hex()
        result["text"] = preview.split(b"\x1a", 1)[0].decode("latin-1")
        return result
