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
### NVIDIA GPU (optional)

`requirements.txt` installs the CPU build of ONNX Runtime, which works everywhere. For CUDA
acceleration use the GPU requirements **instead** (never both):

```bash
pip uninstall -y onnxruntime
pip install -r requirements-gpu.txt
```

ImageForge detects the GPU automatically. If CUDA is not usable (missing drivers, cuDNN, …)
ONNX Runtime silently falls back to the CPU. The right sidebar shows which one is active
(`Model ready · GPU (CUDA)` or `Model ready · CPU`).

### First run needs internet (background removal only)

rembg downloads its neural-network model the first time it is used and caches it in
`~/.u2net/` (Fast ≈ 5 MB, Balanced/Best ≈ 170 MB). The status indicator shows *Loading…*
meanwhile; later runs work offline.

## 2. Run

```bash
python main.py
```

## 3. Using ImageForge

### Importing

| Source | How |
|---|---|
| Files / Explorer / Desktop / Finder | Drag onto the window (anywhere), the drop zone or the list — or **Import Images** (`Ctrl+O`) |
| Folders | Drop a folder (scanned recursively) or *File ▸ Import Folder* (`Ctrl+Shift+O`) |
| Image links | **Import URL** (`Ctrl+U`), drag a link or an image out of a browser, or paste (`Ctrl+V`) |

Supported inputs: PNG, JPG/JPEG, WEBP, BMP, TIFF, GIF (first frame). Links support JPG, JPEG,
PNG and WEBP. Invalid, corrupt, oversized or unsupported files are skipped and listed together
in one friendly dialog. Drag items inside the list to **reorder** them (batches run in list order).
Linux note: Qt 6 needs the XCB libraries. On Debian/Ubuntu:
`sudo apt install libxcb-cursor0 libxkbcommon-x11-0`.

---

### Processing options (right sidebar)

**Background Removal** — *Fast* (U²-Netp), *Balanced* (ISNet) and *Best* (ISNet + alpha
matting for the finest hair detail). The matte is then cleaned (speck removal, alpha snapping)
and edge colours are de-contaminated so no halo of the old background remains. The original
pixels are used for the subject, so full resolution and colour are preserved.

**Resize** — images are **never stretched**. Fill modes:

| Mode | Result |
|---|---|
| **Fit** | Whole image visible, scaled proportionally; output is *at most* W×H (e.g. 1000×500 → 500×250) |
| **Contain** | Scaled inside W×H and padded to **exactly** W×H (transparent; white for JPG/BMP) |
| **Cover** | Fills W×H completely and crops the overflow |
| **Pad Transparent / White / Black** | Like Contain with an explicit padding colour |

*Lock aspect ratio* links width and height using the proportions of the image currently
previewed. *Use image size* copies its dimensions.

**Background Layering** — pick a background image (scaled to *cover* the output, never
stretched). *Subject in front* puts the cut-out on top of the background; *Subject behind*
draws the background **over** the subject, so use a PNG with transparent areas (frames,
overlays). *Center / Top / Bottom* sets the vertical position of the subject. If Resize is off,
the output keeps the subject's original size.

**Moving the subject (fine position)** — with a background layered, switch to the **After**
view and **drag the subject** to where you want it (for example, to dodge a decoration on a
Christmas backdrop). Drag anywhere else to pan the view. The arrow keys nudge it too (click the
preview first; `Shift` = larger steps). The subject can overlap the edges (the overflow is
clipped) but its centre stays on the canvas, so it can't get lost.

* Every image **remembers its own position**; it is saved with the output, exactly as shown.
* **Reset position** puts the selected image back at the Center/Top/Bottom position;
  **Apply to all images** copies its position to every image in the list.
* Positions are stored as a fraction of the output size, so the same nudge lands in the same
  relative spot whatever the output resolution. They only apply while Background Layering is on.
* Moves are undoable (`Ctrl+Z`); a run of arrow-key nudges counts as one step.
* Queued jobs keep the positions they had when you pressed Process.

**Compression** — quality slider 0–100 (0 smallest, 100 best) plus presets: Maximum Quality,
Balanced, Small File, Web Optimized (progressive JPEG). JPEG uses quality + optimised
Huffman tables; PNG uses maximum Deflate and, below quality 70, palette reduction; WebP
supports lossy and **lossless**; TIFF uses lossless Deflate. The live **estimated size**
comes from really encoding the preview image.

**Format conversion** — PNG, JPG, JPEG, WEBP, BMP, TIFF. JPG/JPEG/BMP have no transparency, so
transparent areas are filled with white. Fully opaque images are stored as RGB automatically.

**Output** — default: your real Downloads folder (detected through the Windows Shell API,
XDG on Linux). Files are named `<original>_<W>x<H>.<ext>` using the **final** size, and are
never overwritten:

```
cat.png  → cat_500x500.png   (then cat_500x500_1.png, cat_500x500_2.png, …)
dog.jpg  → dog_1024x768.webp
```

### Processing, queue and undo

* **Process Selected** / **Process All** start a job. Starting another while one runs *queues* it
  (the options are snapshotted per job). **Cancel** stops the running job and drops the queue.
* Each image shows its state in the list: Queued · Processing… · ✔ Saved · ✖ Failed (hover for
  the error). One failing image never stops the batch.
* **Undo** (`Ctrl+Z`) reverts the last operation: import, remove, clear — or a processing run
  (after confirmation, the files it created are deleted).

### Where things are stored

| What | Location |
|---|---|
| Settings (`settings.json`) | Windows `%APPDATA%\ImageForge` · macOS `~/Library/Application Support/ImageForge` · Linux `~/.config/ImageForge` |
| Thumbnail / download cache, logs | Windows `%LOCALAPPDATA%\ImageForge` · macOS `~/Library/Caches/ImageForge` · Linux `~/.cache/ImageForge` |

Cache files older than 3 days (downloads) / 30 days (thumbnails) are pruned at start-up.

---

## 4. Architecture

```
ImageForge/
├── main.py                  entry point, environment checks, theme, exception hooks
├── requirements.txt         CPU dependencies         (requirements-gpu.txt = CUDA variant)
├── core/                    Qt-free imaging engine
│   ├── pipeline.py            stage orchestration, cancellation, preview stage cache
│   ├── resize.py              pure layout maths (fit / contain / cover / pad) — never distorts
│   ├── background.py          rembg wrapper + alpha clean-up + edge de-contamination
│   ├── compositor.py          background scaling, alpha compositing, movable-subject placement
│   └── encoder.py             compression, format conversion, size estimation
├── services/                application services
│   ├── model_manager.py       lazy shared ONNX sessions, GPU→CPU fallback, status events
│   ├── downloader.py          validated http(s) downloads with friendly errors
│   ├── image_loader.py        import validation → ImageItem
│   ├── thumbnail_cache.py     on-disk thumbnails keyed by path+mtime+size
│   ├── output_allocator.py    atomic, collision-free output names
│   ├── history_service.py     undo stack
│   └── settings_service.py    atomic JSON persistence
├── workers/                 everything slow runs here (QThread / QRunnable)
│   ├── batch_worker.py        ThreadPoolExecutor batch with live progress + cancel
│   ├── import_worker.py       parallel validation, thumbnails and downloads
│   ├── model_worker.py        model loading + thread-safe status bridge
│   └── preview_worker.py      Before / After preview tasks
├── models/                  dataclasses and enums (ProcessingOptions, ImageItem, results)
├── ui/                      PyQt6 widgets: main_window, sidebar, image_list, preview_panel,
│                            image_viewer, options_panel, widgets, dnd, dialogs, theme
├── resources/               style.qss (dark theme) and runtime-drawn icons
└── utils/                   paths, logging, formatting, Pillow/Qt image helpers, LRU cache
```

## 5. Performance design

1. **Nothing heavy on the GUI thread.** Import, thumbnailing, downloading, model loading,
   previews and batch processing all run in `QThread`/`QRunnable` workers and report back with
   queued Qt signals.
2. **Parallel
