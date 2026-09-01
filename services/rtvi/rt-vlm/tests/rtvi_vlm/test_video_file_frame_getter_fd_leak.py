# SPDX-FileCopyrightText: Copyright (c) 2026, alphapibeta
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for the GstBus signal-watch fd-leak fix in VideoFileFrameGetter.

Background
----------
Every GStreamer pipeline created by ``VideoFileFrameGetter`` registers its
bus with the GLib main context via ``bus.add_signal_watch()``.  Each watch
pins the ``GstBus`` in the main context and keeps its ``GstPoll`` control
socketpair (2 fds) alive until ``bus.remove_signal_watch()`` is called.

The codec/resolution replacement path in ``get_frames()`` bypasses
``destroy_pipeline()`` and ``_create_pipeline()`` unconditionally overwrites
``self._bus`` with the new pipeline's bus.  Without releasing the old bus's
signal watch on that path, every replacement leaked 2 fds.  Under
codec-alternating load (e.g. JPEG images interleaved with H.265/H.264 clips)
the process eventually exhausts RLIMIT_NOFILE, after which ``gst_poll_new()``
returns NULL and decode wedges permanently.

These tests drive the real ``get_frames()`` code path against a minimal
fake GStreamer/GLib installed in ``sys.modules``.  The fake models the GLib main context's strong pinning of
watched buses: every ``add_signal_watch`` pins one bus (2 fds) and every
``remove_signal_watch`` must unpin it, so a leaked watch shows up exactly
like the production leak.  The decodebin cache, the reconnect guards, and
the flat signal/probe bookkeeping lists are the real ones.

Run (requires no GStreamer, torch, cupy, pyds, or grpc):

    cd services/rtvi/rt-vlm
    python3 -m pytest --noconftest tests/rtvi_vlm/test_video_file_frame_getter_fd_leak.py
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections import deque
from pathlib import Path

import pytest

RT_VLM_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = RT_VLM_ROOT / "src"
MODULE_FILE = SRC_DIR / "vlm_pipeline" / "video_file_frame_getter.py"

_FAKE_MODULE_NAMES = {
    "gi",
    "gi.repository",
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torch.cuda",
    "cupy",
    "cupy.cuda",
    "pyds",
    "grpc",
    "pymediainfo",
    "torchvision",
    "torchvision.transforms",
    "torchvision.transforms.v2",
    "vffg_fdleak_under_test",
}
_MISSING = object()

# Codec/resolution values shared by the tests.
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
CHUNK_END_NS = 2_000_000_000


# ---------------------------------------------------------------------------
# Fake GStreamer / GLib
# ---------------------------------------------------------------------------
#
# The only GStreamer behavior these tests need to model faithfully is the
# GLib main-context pinning of GstBus objects by signal watches.  Everything
# else (elements, pads, links, states) is a no-op bookkeeping stub.


class BusWatchRegistry:
    """Models the GLib main context pinning watched GstBus objects.

    Each active signal watch holds a strong ref to the bus (2 fds via the
    GstPoll control socketpair).  ``fd_count`` is what /proc/<pid>/fd would
    show for these buses.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.active_buses: dict[int, object] = {}
        self.removed_log: list = []
        self.add_count = 0
        self.remove_count = 0

    @property
    def fd_count(self) -> int:
        return 2 * len(self.active_buses)

    def pin(self, bus) -> None:
        key = id(bus)
        if key in self.active_buses:
            raise AssertionError(
                "add_signal_watch() called twice on the same bus without removal"
            )
        self.active_buses[key] = bus
        self.add_count += 1

    def unpin(self, bus) -> None:
        key = id(bus)
        if key not in self.active_buses:
            raise AssertionError(
                "remove_signal_watch() called for a bus that is not (or no longer) watched"
            )
        del self.active_buses[key]
        self.removed_log.append(bus)
        self.remove_count += 1

    def __contains__(self, bus) -> bool:
        return id(bus) in self.active_buses


class _SignalObject:
    """Signal connect/disconnect/emit plumbing shared by elements, pads, buses."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[int, object, tuple]]] = {}
        self._next_handler_id = 1

    def connect(self, signal, callback, *user_data) -> int:
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self._handlers.setdefault(signal, []).append((handler_id, callback, user_data))
        return handler_id

    def disconnect(self, handler_id) -> None:
        for signal in list(self._handlers):
            entries = self._handlers[signal]
            for index, (hid, _, _) in enumerate(entries):
                if hid == handler_id:
                    del entries[index]
                    return
        raise AssertionError(f"disconnect() called for unknown handler id {handler_id}")

    def emit(self, signal, *args) -> None:
        for _, callback, user_data in list(self._handlers.get(signal, [])):
            callback(*args, *user_data)

    @property
    def live_handler_count(self) -> int:
        return sum(len(entries) for entries in self._handlers.values())


class FakeCaps:
    def __init__(self, caps_str: str) -> None:
        self._caps_str = caps_str or ""

    def get_structure(self, _index=0):
        return _CapsStructure(self._caps_str)

    def to_string(self) -> str:
        return self._caps_str


class _CapsStructure:
    def __init__(self, caps_str: str) -> None:
        self._caps_str = caps_str

    def get_name(self) -> str:
        # gst_structure_get_name() on a caps structure returns the full
        # "media/type" name, e.g. "video/x-h264" for "video/x-h264, width=..".
        return self._caps_str.split(",")[0].strip()

    def get_value(self, _key):
        return None

    def get_int(self, _key):
        return (True, 0)

    def get_uint64(self, _key):
        return (True, 0)


class FakePad(_SignalObject):
    def __init__(self, element, name: str) -> None:
        super().__init__()
        self.element = element
        self.name = name
        self.caps_str: str | None = None
        self._probes: dict[int, tuple] = {}
        self._next_probe_id = 1

    def add_probe(self, probe_type, callback, *user_data) -> int:
        probe_id = self._next_probe_id
        self._next_probe_id += 1
        self._probes[probe_id] = (probe_type, callback, user_data)
        return probe_id

    def remove_probe(self, probe_id) -> None:
        if probe_id not in self._probes:
            raise AssertionError(
                f"remove_probe() called for unknown probe id {probe_id}"
            )
        del self._probes[probe_id]

    def query_caps(self, _intersection):
        return FakeCaps(self.caps_str) if self.caps_str else None

    def get_current_caps(self):
        return FakeCaps(self.caps_str or "video/x-raw")

    def link(self, _other) -> bool:
        return True


class FakeElementFactory:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


# Numeric state values (must match the fake Gst.State defined below).
STATE_CHANGE_SUCCESS = 0
STATE_PLAYING = 3


class FakeElement(_SignalObject):
    def __init__(self, factory_name: str) -> None:
        super().__init__()
        self._factory_name = factory_name
        self._state = 0  # NULL
        self._pads: dict[str, FakePad] = {}
        self.properties: dict = {}

    def get_factory(self) -> FakeElementFactory:
        return FakeElementFactory(self._factory_name)

    def get_name(self) -> str:
        return self._factory_name

    def set_state(self, state) -> int:
        self._state = state
        return STATE_CHANGE_SUCCESS

    def get_state(self, _timeout_ns=0):
        return (STATE_CHANGE_SUCCESS, self._state, 0)

    def get_static_pad(self, name: str) -> FakePad:
        if name not in self._pads:
            self._pads[name] = FakePad(self, name)
        return self._pads[name]

    def link(self, _other) -> bool:
        return True

    def set_property(self, key, _value) -> None:
        self.properties[key] = _value

    def find_property(self, _key) -> None:
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeElement {self._factory_name} state={self._state}>"


# Single registry shared by every FakeBus and exposed as
# ``Gst.bus_watch_registry``; reset per test by the autouse fixture.
DEFAULT_REGISTRY = BusWatchRegistry()


class FakeBus(_SignalObject):
    def __init__(self, pipeline) -> None:
        super().__init__()
        self.pipeline = pipeline

    def add_signal_watch(self) -> None:
        DEFAULT_REGISTRY.pin(self)

    def remove_signal_watch(self) -> None:
        DEFAULT_REGISTRY.unpin(self)


class FakePipeline(FakeElement):
    """A pipeline: owns one bus, fires parsebin pad-added once on PLAYING.

    ``video_caps`` is the caps the (real) parsebin would report for the
    source; the test pushes one caps string per pipeline to be created.
    """

    def __init__(self) -> None:
        super().__init__("pipeline")
        self.children: list[FakeElement] = []
        self._bus: FakeBus | None = None
        self.video_caps: str | None = None
        self._pad_added_fired = False
        self.events_sent: list = []

    def add(self, elem) -> bool:
        if elem in self.children:
            return False  # gst_bin_add() on an already-added element
        self.children.append(elem)
        return True

    def remove(self, elem) -> bool:
        if elem in self.children:
            self.children.remove(elem)
            return True
        return False

    def get_bus(self) -> FakeBus:
        if self._bus is None:
            self._bus = FakeBus(self)
        return self._bus

    def seek_simple(self, _format, _flags, _time) -> bool:
        return True

    def send_event(self, event) -> bool:
        self.events_sent.append(event)
        return True

    def set_state(self, state) -> int:
        self._state = state
        if state == STATE_PLAYING and not self._pad_added_fired and self.video_caps:
            self._fire_parsebin_pad_added()
        return STATE_CHANGE_SUCCESS

    def _fire_parsebin_pad_added(self) -> None:
        self._pad_added_fired = True
        parsebin = next(
            (c for c in self.children if c._factory_name == "parsebin"), None
        )
        if parsebin is None:
            return
        pad = parsebin.get_static_pad("src")
        pad.caps_str = self.video_caps
        parsebin.emit("pad-added", parsebin, pad)


class _FakeMainLoop:
    def __init__(self) -> None:
        self._running = False

    def run(self) -> None:
        # Real GStreamer blocks until quit(); return immediately — no bus
        # messages are ever produced, so there is nothing to wait for.
        self._running = True
        self._running = False

    def quit(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running


def _build_fake_gst_module() -> types.ModuleType:
    gst = types.ModuleType("gi.repository.Gst")

    # Time constants.
    gst.NSECOND = 1
    gst.USECOND = 1_000
    gst.MSECOND = 1_000_000
    gst.SECOND = 1_000_000_000
    gst.CLOCK_TIME_NONE = -(2**62)

    class State:
        NULL = 0
        READY = 1
        PAUSED = 2
        PLAYING = 3

    class StateChangeReturn:
        SUCCESS = 0
        NO_PREROLL = 1
        ASYNC = 2
        FAILURE = 3

    class PadProbeType:
        EVENT_DOWNSTREAM = 2
        BUFFER = 4
        QUERY_DOWNSTREAM = 8

    class PadProbeReturn:
        OK = 0
        DROP = 1
        REMOVE = 2

    class SeekFlags:
        NONE = 0
        FLUSH = 1
        KEY_UNIT = 2
        SKIP_TYPEFIND = 4
        ACCURATE = 8
        SNAP_BEFORE = 0x1000
        ANY = 0x10000000

    class Format:
        BYTES = 0
        BUFFER = 1
        TIME = 2
        DEFAULT = 3
        PERCENT = 4

    class MapFlags:
        READ = 0
        WRITE = 1

    class FlowReturn:
        OK = 0
        ERROR = 1

    class MessageType:
        EOS = 6
        ERROR = 8
        WARNING = 9

    class EventType:
        CAPS = 8
        EOS = 15

    class QueryType:
        CUSTOM = 256

    class BufferFlags:
        DELTA_UNIT = 0x8

    class _EventObject:
        def __init__(self, type_) -> None:
            self.type = type_

    class Event:
        @staticmethod
        def new_eos() -> _EventObject:
            return _EventObject(EventType.EOS)

        @staticmethod
        def new_flush_start(_eos=True) -> _EventObject:
            return _EventObject(16)

        @staticmethod
        def new_flush_stop(_eos=False) -> _EventObject:
            return _EventObject(17)

        @staticmethod
        def new_seek(_flags, _format, _time, _range_start, _range_stop) -> _EventObject:
            return _EventObject(1)

    class Caps:
        @staticmethod
        def from_string(caps_str: str) -> FakeCaps:
            return FakeCaps(caps_str)

    class _ElementFactory:
        @staticmethod
        def make(name, *_args) -> FakeElement:
            return FakeElement(name)

        @staticmethod
        def find(_name):
            return None

    registry = DEFAULT_REGISTRY
    pipeline_caps_queue: deque = deque()

    def make_pipeline() -> FakePipeline:
        pipeline = FakePipeline()
        if pipeline_caps_queue:
            pipeline.video_caps = pipeline_caps_queue.popleft()
        return pipeline

    def push_video_caps(caps_str: str) -> None:
        pipeline_caps_queue.append(caps_str)

    def init(*_args) -> None:
        return None

    gst.State = State
    gst.StateChangeReturn = StateChangeReturn
    gst.PadProbeType = PadProbeType
    gst.PadProbeReturn = PadProbeReturn
    gst.SeekFlags = SeekFlags
    gst.Format = Format
    gst.MapFlags = MapFlags
    gst.FlowReturn = FlowReturn
    gst.MessageType = MessageType
    gst.EventType = EventType
    gst.QueryType = QueryType
    gst.BufferFlags = BufferFlags
    gst.Event = Event
    gst.Caps = Caps
    gst.ElementFactory = _ElementFactory
    gst.Pipeline = make_pipeline
    gst.init = init
    gst.bus_watch_registry = registry
    gst.pipeline_caps_queue = pipeline_caps_queue
    gst.push_video_caps = push_video_caps
    return gst


def _build_fake_glib_module() -> types.ModuleType:
    glib = types.ModuleType("gi.repository.GLib")
    glib.MainLoop = _FakeMainLoop
    idle_calls: list = []
    timeout_calls: list = []
    _next_source_id = [1]

    def idle_add(callback, *args) -> int:
        source_id = _next_source_id[0]
        _next_source_id[0] += 1
        idle_calls.append((source_id, callback, args))
        return source_id

    def timeout_add(_interval, callback, *args) -> int:
        source_id = _next_source_id[0]
        _next_source_id[0] += 1
        timeout_calls.append((source_id, callback, args))
        return source_id

    glib.idle_add = idle_add
    glib.timeout_add = timeout_add
    glib.idle_calls = idle_calls
    glib.timeout_calls = timeout_calls
    return glib


def _install_fake_gi() -> dict[str, types.ModuleType | None]:
    """Install the fake gi/gi.repository modules; return the saved originals."""
    saved = {name: sys.modules.get(name) for name in ("gi", "gi.repository")}
    gst_module = _build_fake_gst_module()
    glib_module = _build_fake_glib_module()
    gstpbutils_module = types.ModuleType("gi.repository.GstPbutils")

    repository = types.ModuleType("gi.repository")
    repository.Gst = gst_module
    repository.GLib = glib_module
    repository.GstPbutils = gstpbutils_module

    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda _namespace, _version: None
    gi_module.repository = repository

    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repository
    return saved


# ---------------------------------------------------------------------------
# Third-party stubs (only installed when the real package is unavailable)
# ---------------------------------------------------------------------------


def _stub_if_missing(names: dict[str, types.ModuleType]) -> None:
    for name in names:
        try:
            __import__(name)
        except ImportError:
            parts = name.split(".")
            for i in range(len(parts)):
                subname = ".".join(parts[: i + 1])
                if subname not in sys.modules:
                    sys.modules[subname] = names[subname]
            # Wire the submodule attributes on the parent modules.
            for i in range(1, len(parts)):
                parent_name = ".".join(parts[:i])
                child_name = parts[i]
                setattr(
                    sys.modules[parent_name],
                    child_name,
                    names[".".join(parts[: i + 1])],
                )
        else:
            continue


def _install_third_party_stubs() -> None:
    torch_stub: dict[str, types.ModuleType] = {}
    torch_mod = types.ModuleType("torch")
    torch_nn = types.ModuleType("torch.nn")
    torch_fn = types.ModuleType("torch.nn.functional")
    torch_cuda = types.ModuleType("torch.cuda")
    torch_cuda.empty_cache = lambda: None
    torch_cuda.is_available = lambda: False

    class _Stream:
        def synchronize(self) -> None:
            return None

    torch_cuda.Stream = _Stream
    torch_cuda.stream = lambda _s: _Stream()
    torch_mod.nn = torch_nn
    torch_nn.functional = torch_fn
    torch_mod.cuda = torch_cuda
    torch_mod.Tensor = object
    torch_stub["torch"] = torch_mod
    torch_stub["torch.nn"] = torch_nn
    torch_stub["torch.nn.functional"] = torch_fn
    torch_stub["torch.cuda"] = torch_cuda
    _stub_if_missing(torch_stub)

    cupy_mod = types.ModuleType("cupy")
    cupy_cuda = types.ModuleType("cupy.cuda")

    class _UnownedMemory:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _MemoryPointer:
        def __init__(self, *args, **kwargs) -> None:
            pass

    cupy_cuda.UnownedMemory = _UnownedMemory
    cupy_cuda.MemoryPointer = _MemoryPointer
    cupy_mod.cuda = cupy_cuda
    cupy_mod.ndarray = lambda *_args, **_kwargs: None
    _stub_if_missing({"cupy": cupy_mod, "cupy.cuda": cupy_cuda})

    pyds_mod = types.ModuleType("pyds")
    pyds_mod.get_nvds_buf_surface_gpu = lambda *_args, **_kwargs: (
        None,
        None,
        None,
        None,
        None,
    )
    pyds_mod.configure_source_for_ntp_sync = lambda *_args, **_kwargs: None
    _stub_if_missing({"pyds": pyds_mod})

    _stub_if_missing({"grpc": types.ModuleType("grpc")})

    pymediainfo_mod = types.ModuleType("pymediainfo")

    class MediaInfo:
        def __init__(self, *args, **kwargs) -> None:
            pass

    pymediainfo_mod.MediaInfo = MediaInfo
    _stub_if_missing({"pymediainfo": pymediainfo_mod})

    tv_mod = types.ModuleType("torchvision")
    tv_transforms = types.ModuleType("torchvision.transforms")
    tv_v2 = types.ModuleType("torchvision.transforms.v2")
    tv_mod.transforms = tv_transforms
    tv_transforms.v2 = tv_v2
    _stub_if_missing(
        {
            "torchvision": tv_mod,
            "torchvision.transforms": tv_transforms,
            "torchvision.transforms.v2": tv_v2,
        }
    )


# ---------------------------------------------------------------------------
# Module-under-test loading and fixtures
# ---------------------------------------------------------------------------


def _load_module_under_test():
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    spec = importlib.util.spec_from_file_location("vffg_fdleak_under_test", MODULE_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeFrameSelector:
    """Duck-typed stand-in for DefaultFrameSelector: select every frame."""

    selects_all_frames = True

    def __init__(self) -> None:
        self._selected_pts_array: list = []
        self._selection_end_pts = 0
        self.chunk = None

    def set_chunk(self, chunk) -> None:
        self.chunk = chunk

    def choose_frame(self, _buffer, _pts) -> bool:
        return True


def _save_import_state():
    """Snapshot only module entries and parent attributes this test can touch."""
    saved_modules = {
        name: sys.modules.get(name, _MISSING) for name in _FAKE_MODULE_NAMES
    }
    saved_parent_attrs = {}
    for name in _FAKE_MODULE_NAMES:
        if "." not in name:
            continue
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            saved_parent_attrs[(parent, child_name)] = (
                child_name in vars(parent),
                getattr(parent, child_name, None),
            )
    return saved_modules, saved_parent_attrs


def _restore_import_state(saved_modules, saved_parent_attrs, src_path_was_present):
    """Restore import state after installing fake runtime dependencies."""
    # Restore only the fake modules and package attributes this test can touch.
    # Never roll back unrelated modules imported lazily by other fixtures.
    for name, module in saved_modules.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    for (parent, child_name), (existed, value) in saved_parent_attrs.items():
        if existed:
            setattr(parent, child_name, value)
        else:
            vars(parent).pop(child_name, None)
    if not src_path_was_present and str(SRC_DIR) in sys.path:
        sys.path.remove(str(SRC_DIR))


@pytest.fixture(scope="module")
def vffg():
    """Load video_file_frame_getter.py against the fake GStreamer."""
    saved_modules, saved_parent_attrs = _save_import_state()
    src_path_was_present = str(SRC_DIR) in sys.path
    try:
        _install_fake_gi()
        _install_third_party_stubs()
        yield _load_module_under_test()
    finally:
        _restore_import_state(saved_modules, saved_parent_attrs, src_path_was_present)


@pytest.fixture(autouse=True)
def _reset_gst_state(vffg, monkeypatch):
    """Give every test a clean fake-Gst world and decoder reuse enabled."""
    # Decoder reuse is off by default (DISABLE_DECODER_REUSE defaults to
    # "true"); the reconnect-guard code under test only runs when reuse is
    # enabled, so force it on.
    monkeypatch.setenv("DISABLE_DECODER_REUSE", "false")
    gst = vffg.Gst
    gst.bus_watch_registry.reset()
    gst.pipeline_caps_queue.clear()
    yield


@pytest.fixture()
def fgetter(vffg):
    return vffg.VideoFileFrameGetter(
        frame_selector=FakeFrameSelector(),
        frame_width=FRAME_WIDTH,
        frame_height=FRAME_HEIGHT,
        gpu_id=0,
    )


@pytest.fixture()
def decode(vffg):
    """Run one real get_frames() decode of a file with a given codec/caps."""

    def _decode(fg, file: str, codec: str, caps: str):
        vffg.Gst.push_video_caps(caps)
        chunk = vffg.ChunkInfo(
            file=file,
            chunkIdx=0,
            start_pts=0,
            end_pts=CHUNK_END_NS,
        )
        return fg.get_frames(chunk, video_codec=codec)

    return _decode


def live_handler_count(elem) -> int:
    return elem.live_handler_count


def active_bus_count(registry) -> int:
    return len(registry.active_buses)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_codec_change_releases_previous_bus_watch(fgetter, decode, vffg):
    """Core regression: the replacement path must release the old bus watch.

    Without the fix, the second decode leaves the first pipeline's bus
    pinned in the GLib main context: 2 active watches / 4 fds.
    """
    registry = vffg.Gst.bus_watch_registry
    decode(fgetter, "f1.mp4", "h264", "video/x-h264")
    old_bus = fgetter._bus
    decode(fgetter, "f2.mp4", "h265", "video/x-h265")

    # Only the new pipeline's bus may be watched.
    assert active_bus_count(registry) == 1, (
        f"leaked {active_bus_count(registry) - 1} old bus watch(es); {registry.fd_count} fds still pinned"
    )
    assert old_bus not in registry
    assert old_bus in registry.removed_log
    # The new pipeline's watch is live.
    assert fgetter._bus is not None
    assert fgetter._bus is not old_bus
    assert fgetter._bus in registry
    assert fgetter._bus_signal_watch_added is True


def test_multi_cycle_alternating_codecs_never_grows(fgetter, decode, vffg):
    """The production failure mode: JPEG/H.265/H.264-alternating load.

    Every codec change replaces the pipeline; each replacement must release
    the previous bus watch so the number of active watches (and pinned fds)
    stays constant no matter how many decodes happen.
    """
    registry = vffg.Gst.bus_watch_registry
    cycles = [
        ("a.mp4", "h264", "video/x-h264"),
        ("b.mp4", "h265", "video/x-h265"),
        ("c.jpg", "JPEG", "image/jpeg"),
        ("d.mp4", "h264", "video/x-h264"),
        ("e.mp4", "h265", "video/x-h265"),
        ("f.jpg", "JPEG", "image/jpeg"),
        ("g.mp4", "h264", "video/x-h264"),
        ("h.mp4", "h265", "video/x-h265"),
    ]
    baseline = None
    for index, (file, codec, caps) in enumerate(cycles):
        decode(fgetter, file, codec, caps)
        assert active_bus_count(registry) == 1, (
            f"cycle {index} ({codec}): {active_bus_count(registry) - 1} bus watch(es) leaked"
        )
        assert registry.fd_count == 2, f"cycle {index}: {registry.fd_count} fds pinned"
        # The flat bookkeeping lists must not accumulate stale entries that
        # pin old pipelines' elements.
        if baseline is None:
            baseline = (
                len(fgetter._gst_signal_handler_ids),
                len(fgetter._gst_pad_probe_ids),
            )
        else:
            assert len(fgetter._gst_signal_handler_ids) <= baseline[0], (
                f"cycle {index}: signal handler list grew to "
                f"{len(fgetter._gst_signal_handler_ids)} (baseline {baseline[0]})"
            )
            assert len(fgetter._gst_pad_probe_ids) <= baseline[1], (
                f"cycle {index}: pad probe list grew to {len(fgetter._gst_pad_probe_ids)} (baseline {baseline[1]})"
            )
    # One watch added per created pipeline, all old ones removed.
    assert registry.add_count == len(cycles)
    assert registry.remove_count == len(cycles) - 1


def test_cached_decodebin_handlers_reconnected_exactly_once(fgetter, decode):
    """Reconnect guard: after a disconnect, handlers come back exactly once."""
    decode(fgetter, "f1.mp4", "h264", "video/x-h264")
    h264_db = fgetter._vdecodebin_cache[("h264", FRAME_WIDTH, FRAME_HEIGHT)]
    # pad-added + deep-element-added.
    assert live_handler_count(h264_db) == 2

    decode(fgetter, "f2.mp4", "h265", "video/x-h265")
    # The replacement path disconnects ALL handlers, including the cached
    # decoder's, and clears the signal-key set.
    assert live_handler_count(h264_db) == 0
    assert (
        "h264",
        FRAME_WIDTH,
        FRAME_HEIGHT,
    ) not in fgetter._vdecodebin_cache_signal_keys

    decode(fgetter, "f3.mp4", "h264", "video/x-h264")
    # Re-entering the pipeline reconnects exactly once — not zero, not four.
    assert live_handler_count(h264_db) == 2
    assert ("h264", FRAME_WIDTH, FRAME_HEIGHT) in fgetter._vdecodebin_cache_signal_keys


def test_nulling_bus_before_disconnect_silently_leaks_watch(fgetter, decode, vffg):
    """Characterization of the hazard: bus ref nulled first => no removal."""
    registry = vffg.Gst.bus_watch_registry
    decode(fgetter, "f1.mp4", "h264", "video/x-h264")
    bus = fgetter._bus
    fgetter._bus = None  # simulate the wrong ordering
    fgetter._disconnect_gst_callbacks()
    # The removal was skipped: the bus stays pinned (this is the bug the
    # fix must not reintroduce).
    assert bus in registry
    assert bus not in registry.removed_log
    assert registry.fd_count == 2


def test_import_cleanup_preserves_unrelated_modules():
    """Cleanup must not remove modules imported by other pytest fixtures."""
    saved_modules, saved_parent_attrs = _save_import_state()
    unrelated_name = "unrelated_lazy_module"
    unrelated = types.ModuleType(unrelated_name)
    sys.modules[unrelated_name] = unrelated
    try:
        _restore_import_state(
            saved_modules, saved_parent_attrs, src_path_was_present=True
        )
        assert sys.modules[unrelated_name] is unrelated
    finally:
        sys.modules.pop(unrelated_name, None)
