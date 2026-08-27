######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

"""The Cython-protected EVS overlay cannot be turned back into Python source.

``docker/rtvi_vlm/patches/protect_vllm_evs_overlay.py`` compiles the EVS overlay
modules that were installed into the ``vllm`` package into native extension
modules and deletes the ``.py`` sources. These tests assert the property that
motivates that step: nothing recoverable is left behind for a decompiler.

The three recovery vectors a de-obfuscator would use are:

1. the original ``.py`` file,
2. CPython bytecode (``.pyc`` / ``__pycache__``), which ``uncompyle6``-class
   tools turn back into readable Python,
3. the Cython-generated ``.c`` intermediate, which is a direct transliteration
   of the module.

Each must be gone, and the shipped ``.so`` must contain neither the statement
text of the module nor a usable code object.

Two tiers are covered:

* ``TestCompiledOverlayCannotBeDeobfuscated`` compiles a synthetic overlay
  module carrying known markers and attacks the result. It needs Cython and a C
  compiler, so it skips in the shipped runtime image (Cython is uninstalled at
  the end of the build).
* ``TestInstalledOverlayIsProtected`` attacks the artifacts that actually ship,
  using the repo overlay sources as the known plaintext. It runs wherever a
  protected vLLM install is present -- i.e. inside the built rtvi_vlm image.

Known and intentional limits of Cython protection, pinned by
``test_string_literals_and_docstrings_survive``: string constants and docstrings
must survive into the binary, and identifier names leak through the extension's
introspection tables. Only executable logic is protected -- never put a secret
in a literal in these modules.
"""

from __future__ import annotations

import contextlib
import dis
import importlib
import importlib.util
import inspect
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PATCHES_DIR = REPO_ROOT / "docker" / "rtvi_vlm" / "patches"
PROTECT_SCRIPT = PATCHES_DIR / "protect_vllm_evs_overlay.py"
OVERLAY_SRC_DIR = PATCHES_DIR / "evs_vllm_files"

# A rel path from the script's own allowlist, so the synthetic module travels the
# same code path as a real overlay module.
FAKE_REL_PATH = "multimodal/evs.py"

COMMENT_MARKER = "EVS_PROTECTION_COMMENT_MARKER"
STATEMENT_MARKER = "pruned_total = keep_ratio * frame_count - drop_bias"
LITERAL_MARKER = "EVS_PROTECTION_LITERAL_MARKER"
DOCSTRING_MARKER = "EVS_PROTECTION_DOCSTRING_MARKER"

FAKE_OVERLAY_SOURCE = f'''"""{DOCSTRING_MARKER}"""

TUNING_TABLE = "{LITERAL_MARKER}"


def compute_pruning(frame_count, keep_ratio):
    # {COMMENT_MARKER} proprietary heuristic
    drop_bias = 7
    {STATEMENT_MARKER}
    return max(0, int(pruned_total))
'''

# Linux-only: the artifact under test is an ELF ``.so`` inside the rtvi_vlm
# image, and the assertions below check for ELF magic. ``cc`` also resolves on
# macOS, so gate on the platform rather than on the compiler alone.
_HAS_TOOLCHAIN = (
    sys.platform.startswith("linux")
    and importlib.util.find_spec("Cython") is not None
    and bool(shutil.which("cc") or shutil.which("gcc"))
)

requires_cython = pytest.mark.skipif(
    not _HAS_TOOLCHAIN,
    reason=(
        "needs Linux plus Cython and a C compiler; Cython is absent from the shipped "
        "runtime image"
    ),
)

pytestmark = pytest.mark.no_gpu


def _load_protect_module(name_suffix: str = "under_test"):
    """Import the protection script fresh so it re-reads its env-driven globals."""
    spec = importlib.util.spec_from_file_location(
        f"protect_vllm_evs_overlay_{name_suffix}", PROTECT_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _isolated_vllm_imports(site_packages: Path):
    """Import ``vllm.*`` from ``site_packages`` without disturbing a real install."""
    saved = {
        key: mod for key, mod in sys.modules.items() if key == "vllm" or key.startswith("vllm.")
    }
    for key in saved:
        del sys.modules[key]
    sys.path.insert(0, str(site_packages))
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(site_packages))
        for key in [k for k in sys.modules if k == "vllm" or k.startswith("vllm.")]:
            del sys.modules[key]
        sys.modules.update(saved)


@pytest.fixture
def protected_fake_overlay(tmp_path, monkeypatch):
    """Compile a synthetic overlay module exactly the way the image build does."""
    site_packages = tmp_path / "site-packages"
    vllm_root = site_packages / "vllm"
    module_dir = vllm_root / Path(FAKE_REL_PATH).parent
    module_dir.mkdir(parents=True)
    (vllm_root / "__init__.py").write_text("")
    (module_dir / "__init__.py").write_text("")

    installed_source = vllm_root / FAKE_REL_PATH
    installed_source.write_text(FAKE_OVERLAY_SOURCE)

    overlay_dir = tmp_path / "evs_vllm_files"
    (overlay_dir / Path(FAKE_REL_PATH).parent).mkdir(parents=True)
    (overlay_dir / FAKE_REL_PATH).write_text(FAKE_OVERLAY_SOURCE)

    build_dir = tmp_path / "cython-build"

    monkeypatch.setenv("VLLM_EVS_PROTECTION", "cython")
    monkeypatch.setenv("VLLM_EVS_TARGET", str(vllm_root))
    monkeypatch.setenv("VLLM_EVS_CYTHON_BUILD_DIR", str(build_dir))
    # The build's own smoke import is exercised by the image build, not here.
    monkeypatch.setenv("VLLM_EVS_CYTHON_SMOKE_IMPORT", "0")

    protect = _load_protect_module()
    monkeypatch.setattr(protect, "EVS_FILES_DIR", overlay_dir)
    protect.main()

    extension = protect._extension_path(installed_source)
    assert extension is not None, "protection step produced no native extension"

    return {
        "site_packages": site_packages,
        "module_dir": module_dir,
        "source": installed_source,
        "extension": extension,
        "build_dir": build_dir,
    }


@requires_cython
class TestCompiledOverlayCannotBeDeobfuscated:
    """Attack a freshly protected module with the tools a de-obfuscator would use."""

    def test_python_source_is_removed(self, protected_fake_overlay):
        assert not protected_fake_overlay["source"].exists()

    def test_no_bytecode_is_left_behind(self, protected_fake_overlay):
        # A stale .pyc is decompilable back to near-original Python.
        module_dir = protected_fake_overlay["module_dir"]
        assert not (module_dir / "__pycache__").exists()
        assert list(module_dir.rglob("*.pyc")) == []

    def test_no_c_intermediate_is_left_behind(self, protected_fake_overlay):
        # The Cython .c file transliterates the module and would defeat the point.
        module_dir = protected_fake_overlay["module_dir"]
        assert list(module_dir.glob("*.c")) == []
        assert list(module_dir.glob("*.pyx")) == []
        assert not protected_fake_overlay["build_dir"].exists()

    def test_binary_is_native_and_holds_no_statement_text(self, protected_fake_overlay):
        blob = protected_fake_overlay["extension"].read_bytes()

        assert blob[:4] == b"\x7fELF", "expected a native extension, not a renamed source file"
        assert COMMENT_MARKER.encode() not in blob
        assert STATEMENT_MARKER.encode() not in blob
        assert FAKE_OVERLAY_SOURCE.encode() not in blob

    def test_compiled_module_exposes_no_recoverable_bytecode(self, protected_fake_overlay):
        with _isolated_vllm_imports(protected_fake_overlay["site_packages"]):
            module = importlib.import_module("vllm.multimodal.evs")

            assert module.__file__.endswith(protected_fake_overlay["extension"].suffix)
            assert isinstance(module.__loader__, importlib.machinery.ExtensionFileLoader)

            func = module.compute_pruning
            # Cython's binding mode publishes a placeholder code object for
            # signature introspection. It must stay a placeholder: real bytecode
            # here would hand a decompiler the function body.
            code = getattr(func, "__code__", None)
            if code is not None:
                assert set(code.co_code) == {0}, "compiled function exposes real bytecode"
                assert code.co_consts == ()
                assert code.co_names == ()
                assert (
                    not Path(code.co_filename).is_absolute() or not Path(code.co_filename).exists()
                )

            opcodes = {instruction.opcode for instruction in dis.get_instructions(func)}
            assert opcodes <= {0}, f"disassembly recovered real opcodes: {opcodes}"

    def test_inspect_cannot_recover_source(self, protected_fake_overlay):
        with _isolated_vllm_imports(protected_fake_overlay["site_packages"]):
            module = importlib.import_module("vllm.multimodal.evs")

            with pytest.raises((OSError, TypeError)):
                inspect.getsource(module)
            with pytest.raises((OSError, TypeError)):
                inspect.getsource(module.compute_pruning)
            assert inspect.getsourcefile(module) is None

    def test_literals_docstrings_and_signatures_survive(self, protected_fake_overlay):
        """Pin the documented limit: only executable logic is protected."""
        blob = protected_fake_overlay["extension"].read_bytes()

        assert LITERAL_MARKER.encode() in blob, "string constants must remain usable at runtime"
        assert DOCSTRING_MARKER.encode() in blob
        # embedsignature=True keeps parameter names, annotations and defaults.
        assert b"compute_pruning(frame_count, keep_ratio)" in blob


# The installed tier runs wherever a protected vLLM install is found, which is
# the Linux rtvi_vlm image today. Match the running platform's own native-binary
# magic rather than asserting ELF unconditionally, so the check keeps testing
# "this is a compiled binary, not a renamed source file" instead of failing on
# the platform. An unknown platform contributes no magic and skips the check.
_NATIVE_MAGICS: tuple[bytes, ...]
if sys.platform.startswith("linux"):
    _NATIVE_MAGICS = (b"\x7fELF",)
elif sys.platform == "darwin":
    # Mach-O thin (32/64-bit, both endiannesses) and universal binaries.
    _NATIVE_MAGICS = (
        b"\xcf\xfa\xed\xfe",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xfe\xed\xfa\xce",
        b"\xca\xfe\xba\xbe",
    )
elif sys.platform == "win32":
    _NATIVE_MAGICS = (b"MZ",)  # PE/COFF .pyd
else:
    _NATIVE_MAGICS = ()


def _installed_vllm_root() -> Path | None:
    protect = _load_protect_module("locate")
    if protect.VLLM_ROOT.is_dir():
        return protect.VLLM_ROOT
    spec = importlib.util.find_spec("vllm")
    if spec is not None and spec.origin:
        return Path(spec.origin).parent
    return None


def _protected_installed_modules() -> list[tuple[str, Path, Path]]:
    """(rel_path, installed .so, repo overlay source) for every protected module."""
    vllm_root = _installed_vllm_root()
    if vllm_root is None:
        return []

    protect = _load_protect_module("locate")
    found = []
    for rel_path in sorted(protect.EVS_ONLY_REL_PATHS):
        installed_path = vllm_root / rel_path
        extension = protect._extension_path(installed_path)
        if extension is not None:
            found.append((rel_path, extension, OVERLAY_SRC_DIR / rel_path))
    return found


_INSTALLED_MODULES = _protected_installed_modules()

requires_protected_install = pytest.mark.skipif(
    not _INSTALLED_MODULES,
    reason="no Cython-protected EVS overlay installed (run inside a built rtvi_vlm image)",
)


def _plaintext_probes(source_path: Path) -> tuple[list[str], list[str]]:
    """Comment lines and quote-free code lines that must not survive compilation."""
    comments: list[str] = []
    statements: list[str] = []
    in_docstring = False
    for raw_line in source_path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        # Docstring bodies are kept by Cython on purpose, so skip over them.
        if in_docstring:
            in_docstring = '"""' not in line and "'''" not in line
            continue
        if line.startswith(('"""', "'''")) and line.count(line[:3]) == 1:
            in_docstring = True
            continue
        if line.startswith("#") and len(line) > 20:
            comments.append(line)
        elif (
            len(line) >= 30
            # Indented, so it is a statement inside a block rather than a
            # top-level declaration.
            and raw_line.startswith(" ")
            and not line.startswith(("#", "def ", "class ", "@", "import ", "from ", ")", "]", "}"))
            and '"' not in line
            and "'" not in line
            # ``:`` and ``->`` appear in annotations and defaults, which
            # ``embedsignature=True`` deliberately keeps in the binary; skip
            # them so the probe only covers executable logic.
            and ":" not in line
            and "->" not in line
            and not line.endswith(",")
            and ("=" in line or "(" in line)
        ):
            statements.append(line)
    return comments[:25], statements[:25]


@requires_protected_install
class TestInstalledOverlayIsProtected:
    """Attack the artifacts that actually ship, using the repo overlay as plaintext."""

    @pytest.mark.parametrize("rel_path,extension,_source", _INSTALLED_MODULES)
    def test_no_python_source_or_backup_remains(self, rel_path, extension, _source):
        installed_source = extension.with_name(Path(rel_path).name)

        assert not installed_source.exists(), f"{rel_path} still ships as Python source"
        assert not installed_source.with_suffix(".py.orig").exists()

    @pytest.mark.parametrize("rel_path,extension,_source", _INSTALLED_MODULES)
    def test_no_bytecode_or_c_intermediate_remains(self, rel_path, extension, _source):
        stem = Path(rel_path).stem
        module_dir = extension.parent

        assert not (module_dir / f"{stem}.c").exists()
        assert list((module_dir / "__pycache__").glob(f"{stem}.*.pyc")) == []

    @pytest.mark.parametrize("rel_path,extension,source", _INSTALLED_MODULES)
    def test_binary_holds_no_overlay_statement_text(self, rel_path, extension, source):
        if not source.is_file():
            pytest.skip(f"no overlay source in repo for {rel_path}")

        blob = extension.read_bytes()
        if _NATIVE_MAGICS:
            assert blob.startswith(
                _NATIVE_MAGICS
            ), "expected a native extension, not a renamed source file"

        comments, statements = _plaintext_probes(source)
        # Guard against a vacuous pass if the probe heuristics ever stop matching.
        assert len(comments) + len(statements) >= 3, f"no usable plaintext probes in {source}"

        leaked = [line for line in comments + statements if line.encode() in blob]
        assert not leaked, f"{rel_path} binary leaks source text: {leaked[:3]}"
