# VideoTimeline Demo

This demo showcases the VideoTimeline custom Gradio component with interactive video playback and timeline markers.

## Setup Instructions

### Creating a virtual environment
1. Create and activate virtual environment
```bash
python3 -m venv videotimeline-env
source videotimeline-env/bin/activate
```

2. Install all the dependencies for this custom component
```
gradio cc install
```

### Adding Your Video

Since video files are not included in the source code, you'll need to provide your own video file:

1. **Create the media directory** (if it doesn't exist):
   ```bash
   cd demo
   mkdir -p media
   ```

2. **Add your video file** to the `media` directory:
   - Place your video file (e.g., `your_video.mp4`) in the `demo/media/` directory
   - Supported formats: MP4, WebM, and other common video formats

3. **Update the video path** in `app.py`:
   - Open `app.py`
   - Locate the `example` dictionary (around line 17)
   - Update the `"video"` field with your filename:
     ```python
     example = {
         "video": os.path.join(MEDIA_DIR, "your_video.mp4"),  # Change this
         ...
     }
     ```

4. **Adjust timestamps and markers** (optional):
   - Update `timestamps`, `marker_labels`, `start_times`, `end_times`, and `descriptions` to match your video content
   - Ensure all timestamp values are within your video's duration

## Running the Demo

Once you've added your video file and updated the configuration:

```bash
python app.py
```

The demo will be available at `http://localhost:7860` or `http://0.0.0.0:7860`.

## Example Configuration

```python
example = {
    "video": os.path.join(MEDIA_DIR, "your_video.mp4"),
    "subtitles": None,
    'timestamps': [2, 5, 10, 15, 20, 28],  # Timeline markers in seconds
    'marker_labels': ['Aa', 'Bb', 'Cc', 'Dd', 'Ee', 'Ff'],  # Labels for markers
    'start_times': [2, 5, 10, 15, 25, 28],  # Start times for segments
    'end_times': [3, 8, 13, 20, 28, 30],  # End times for segments
    'descriptions': ['Description 1', 'Description 2', ...]  # Segment descriptions
}
```

## Troubleshooting

- **Video not loading?** Check that the video file path is correct and the file exists in the `media` directory
- **Markers not appearing?** Ensure timestamp values are within the video duration
