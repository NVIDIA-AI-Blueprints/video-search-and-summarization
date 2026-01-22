# Video Timeline - Gradio Custom Component

User can import the Gradio custom component as follows:

```python
import gradio as gr
from gradio_videotimeline import VideoTimeline

with gr.Blocks() as demo:
    video_timeline = VideoTimeline(
        value={
            "video": "https://github.com/gradio-app/gradio/raw/main/demo/video_component/files/world.mp4",
            "subtitles": None,
            "timestamps": [1, 10, 20],
            "marker_labels": ["A", "B", "C"],
            "start_times": [1, 10, 20],
            "end_times": [5, 14, 25],
            "descriptions": ["Event A", "Event B", "Event C"]
        }
    )
```

## BUILD on TOP of this project - Starter Steps

1. Install Node.js 23.x: Refer to https://github.com/nodesource/distributions?tab=readme-ov-file#installation-instructions-deb

2. Install virtual env pkg
```bash
sudo apt install python3.12-venv
```

3. Create and activate virtual environment
```bash
python3 -m venv ../videotimeline-env
source ../videotimeline-env/bin/activate
```

4. Install Gradio 5.49.1 dependency
```bash
pip3 install gradio==5.49.1
```

5. Install other dependencies using Gradio CLI
```bash
gradio cc install
```

6. Develop and test your Gradio app using the following command:
```bash
gradio cc dev
```

7. Build the Gradio app to generate the wheel file:
```bash
gradio cc build
```

8. The wheel file will be generated in the `dist` directory and can be copied to the ``vss-engine/binaries`` directory.

