# ImageForge

A Python-based desktop application for intelligent image processing, batch editing and image optimisation.

ImageForge combines AI-powered background removal with flexible image resizing, background compositing, compression and format conversion in one desktop application.

## Features

- **AI Background Removal** – Automatically remove image backgrounds using neural network models.
- **Batch Processing** – Import and process multiple images simultaneously.
- **Smart Resizing** – Resize images without stretching or distorting their proportions.
- **Background Compositing** – Add custom backgrounds and adjust subject positioning.
- **Image Compression** – Optimise image sizes while maintaining visual quality.
- **Format Conversion** – Convert between PNG, JPG, WEBP, BMP and TIFF.
- **Live Preview** – Compare original and processed images before exporting.
- **GPU Acceleration** – Optional NVIDIA GPU support with automatic CPU fallback.
- **Processing Queue** – Run multiple image processing jobs without freezing the interface.

## Technologies Used

| Technology | Purpose |
|---|---|
| Python | Core application development |
| PyQt6 | Desktop graphical user interface |
| Pillow | Image manipulation and processing |
| OpenCV | Image processing operations |
| NumPy | Numerical and array operations |
| rembg | AI-powered background removal |
| ONNX Runtime | Neural network inference and GPU acceleration |

## Application Screenshots

<!-- Add screenshots of ImageForge here -->

## Installation

**Requirements:** Python 3.12 or newer recommended.

1. Clone the repository:

```bash
git clone YOUR_REPOSITORY_URL
cd ImageForge
```

2. Create a virtual environment:

```bash
python -m venv .venv
```

3. Activate the environment:

Windows PowerShell:
```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux:
```bash
source .venv/bin/activate
```

4. Install dependencies:

```bash
pip install -r requirements.txt
```

## Running the Application

Start ImageForge using:

```bash
python main.py
```

The first background removal operation requires an internet connection to download the required AI model. Subsequent operations can run offline.

## GPU Support (Optional)

ImageForge supports NVIDIA GPU acceleration through ONNX Runtime.

For GPU support, install the GPU dependencies instead of the CPU version:

```bash
pip uninstall -y onnxruntime
pip install -r requirements-gpu.txt
```

Compatible NVIDIA drivers and CUDA/cuDNN dependencies are required. ImageForge can fall back to CPU processing when GPU acceleration is unavailable.

## Project Structure

```text
ImageForge/
├── core/        # Image processing engine
├── models/      # Data models and settings
├── resources/   # Application styling and icons
├── services/    # Application services
├── ui/          # PyQt6 interface components
├── utils/       # Shared utilities
├── workers/     # Background processing tasks
│
├── main.py
├── requirements.txt
└── requirements-gpu.txt
```

## Development

ImageForge was developed as a modular desktop application, with image processing operations separated from the graphical interface. Background workers handle resource-intensive tasks to keep the application responsive during processing.

