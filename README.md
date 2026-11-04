# ImageForge

A desktop app for **batch image processing**: AI background removal, distortion-free resizing,
custom background compositing, compression and format conversion. Built with Python, PyQt6,
Pillow, OpenCV, NumPy and [rembg](https://github.com/danielgatis/rembg).

* Drag & drop files, folders and image links; import hundreds of images at once
* Live **Before / After** preview with zoom, pan and an estimated output file size
* Parallel processing with a progress bar, cancellation and a job queue — the UI never freezes
---
## 1. Installation

Requires **Python 3.12+** (the version this project was developed and tested on; `main.py` warns below 3.12 and refuses to start below 3.10).

```bash
# 1. get into the project folder
cd ImageForge

# 2. create and activate a virtual environment
python -m venv .venv
#   Windows (PowerShell):  .venv\Scripts\Activate.ps1
#   Windows (cmd):         .venv\Scripts\activate.bat

# 3. install the dependencies
pip install --upgrade pip
pip install -r requirements.txt
```
