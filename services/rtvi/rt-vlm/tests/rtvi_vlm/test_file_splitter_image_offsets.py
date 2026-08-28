from types import SimpleNamespace

from utils.file_splitter import FileSplitter


def test_image_list_preserves_requested_offsets():
    chunks = []
    FileSplitter(
        "first.jpg;second.jpg",
        FileSplitter.SplitMode.SEEK,
        chunk_duration_sec=2,
        start_pts=1_000_000_000,
        end_pts=3_000_000_000,
        media_file_info=SimpleNamespace(is_image=True),
        on_new_chunk=chunks.append,
    ).split()

    assert len(chunks) == 2
    assert chunks[0].start_pts == 1_000_000_000
    assert chunks[0].end_pts == 3_000_000_000
    assert chunks[1] is None
