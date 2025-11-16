import os
import sys
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, File, UploadFile, HTTPException
from pydantic import BaseModel, Field
import base64
import io
from PIL import Image

# Local imports
from edge_node.config_loader import load_edge_config, EdgeConfig

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_cv")

# --- Global Setup ---

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"

config: Optional[EdgeConfig] = None

def load_config():
    """Loads configuration."""
    global config
    if config is None:
        try:
            config = load_edge_config(CONFIG_PATH, SCHEMA_PATH)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}")
            raise RuntimeError("Configuration failed to load.") from e

# --- Pydantic Schemas for API ---

class BoundingBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int

class Detection(BaseModel):
    label: str = Field(..., description="Detected object class label.")
    confidence: float = Field(..., description="Confidence score of the detection.")
    bbox: List[int] = Field(..., description="Bounding box [x1, y1, x2, y2].")
    track_id: Optional[str] = Field(None, description="Optional track ID for tracking.")

class InferenceResult(BaseModel):
    detections: List[Detection] = Field(default_factory=list, description="List of all detected objects.")
    model_version: str = Field("mock-v1.0", description="Version of the model used for inference.")

# --- Core CV Logic (Mocked) ---

class CVEngine:
    """
    Wrapper for the actual detector + tracker inference model.
    In a real implementation, this would load the TensorRT engine or PyTorch model.
    """
    def __init__(self):
        self.model_version = "mock-v1.0"
        logger.info(f"CV Engine initialized with version: {self.model_version}")

    def infer(self, image: Image.Image) -> InferenceResult:
        """
        Performs mock inference on the image.
        """
        logger.info(f"Performing mock inference on image of size {image.size}")
        
        # Mock detection result
        mock_detections = [
            Detection(
                label="person",
                confidence=0.95,
                bbox=[100, 100, 200, 300],
                track_id="t-001"
            ),
            Detection(
                label="car",
                confidence=0.88,
                bbox=[500, 400, 700, 500],
                track_id="t-002"
            )
        ]
        
        return InferenceResult(detections=mock_detections, model_version=self.model_version)

    def reload_model(self, new_version: str):
        """
        Simulates reloading the model after a sync update.
        """
        self.model_version = new_version
        logger.info(f"CV Engine model reloaded to version: {self.model_version}")

# Initialize the CV Engine globally
cv_engine = CVEngine()

# --- FastAPI Application ---

app = FastAPI(
    title="VSS CV Service",
    on_startup=[load_config]
)

@app.post("/infer", response_model=InferenceResult)
async def infer_image(file: UploadFile = File(...)):
    """
    Accepts an image file (multipart/form-data) and returns detection JSON.
    """
    try:
        # Read the uploaded file content
        image_data = await file.read()
        
        # Open the image using PIL
        image = Image.open(io.BytesIO(image_data))
        
        # Perform inference
        result = cv_engine.infer(image)
        
        return result
    except Exception as e:
        logger.error(f"Inference failed: {e}")
        raise HTTPException(status_code=500, detail=f"Inference failed: {e}")

@app.post("/_reload")
async def reload_model(new_version: str):
    """
    Internal endpoint to trigger a model reload (used by vss_sync).
    """
    cv_engine.reload_model(new_version)
    return {"message": f"Model reload initiated for version {new_version}"}

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "model_version": cv_engine.model_version}

# --- Main Entry Point ---

if __name__ == "__main__":
    import uvicorn
    # The CV service runs on port 8001
    uvicorn.run(app, host="0.0.0.0", port=8001)
