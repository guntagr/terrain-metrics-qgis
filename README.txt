# QGIS DTM Plot Metrics Pipeline

This repository contains a QGIS Python Console script for a **one-pass** workflow that:

- clips digital terrain models (DTMs) by plot polygons,
- aligns all DTMs to a common reference raster, and
- computes a set of per-plot terrain metrics.

The script is intended for forest / environmental applications where many DTMs (e.g. UAV LiDAR and ALS) must be processed consistently for a network of inventory plots.

---

## Features

- Batch processing of all DTMs in a folder (`DTM_ROOT` / `SRC_DTM_ROOT`).
- Plot-based clipping using a polygon layer with an ID field.
- Alignment of all rasters to a reference DTM grid (extent, resolution, CRS).
- Per-plot terrain metrics using SAGA GIS algorithms, including:
  - real surface area (AR),
  - roughness,
  - topographic openness,
  - terrain ruggedness index (TRI),
  - vector ruggedness measure (VRM),
  - “basic terrain analysis” outputs (e.g. slope/aspect and related grids).
- Configurable neighbourhood radii and filename patterns.
- Output organised by metric and by plot in a reproducible folder structure.

---

## Requirements

- **QGIS 3.x** (with the Processing toolbox enabled)
- **SAGA GIS** provider enabled in QGIS  
  (Processing → Options → Providers → check “SAGA” / “SAGA Next Gen”)
- **GDAL** (comes with QGIS; used via `osgeo.gdal`)
- A QGIS project is optional: the script works with file paths, but it must be run **inside** QGIS so that `processing` and `Qgs*` classes are available.

---

## Input data

Configure the paths at the top of the script:

- `DTM_ROOT`  
  Folder containing DTMs to be processed (GeoTIFF or ASCII grid).

- `SRC_DTM_ROOT`  
  Folder with **source** DTMs for clipping/alignment.  
  Set equal to `DTM_ROOT` if you use the same rasters.

- `MASK_SHAPE`  
  Vector polygon layer (e.g. 30 × 30 m plots) used to clip DTMs.

- `ID_FIELD`  
  Name of the field in `MASK_SHAPE` that uniquely identifies each plot.

- `REF_PATH`  
  Reference DTM used to define target grid (extent, resolution, CRS).

- `OUT_BASE`  
  Output root folder. All results and temporary rasters are written here.

- `RADII_M`  
  List of radii (in metres) for neighbourhood-based metrics (VRM, openness, TRI, etc.).

- `CHANNEL_DENSITY`  
  Density parameter passed to SAGA “Basic terrain analysis” (if applicable).

- `FILENAME_MODE`, `DTM_PREFIX`, `SENSOR_TAG`, `RES_TAG`  
  Options that control how output filenames are constructed (e.g. prefixing with
  `"UAV_LiDAR"` or `"ALS"` and adding a resolution tag).

All paths are plain strings and can be edited directly in the script.

---

## Output structure

Within `OUT_BASE` the script creates folders similar to:

- `1_DTM/` – per-plot aligned DTM clips  
- `2_ROUGHNESS_by_plots_aligned/`  
- `3_AR_by_plots_aligned/`  
- `4_OPENNESS_by_plots_aligned/`  
- `5_TRI_by_plots_aligned/`  
- `6_VRM_by_plots_aligned/`  
- `7_BASIC_TERRAIN_by_plots_aligned/`  

Each of these contains subfolders per plot (e.g. `Plot1`, `Plot2`, …), and
inside those, GeoTIFFs named according to the selected filename mode.

Temporary files are written to `_tmp*` subfolders inside `OUT_BASE`.

---

## How to run the script

1. **Open QGIS 3.x.**

2. Ensure the **SAGA** provider is enabled:  
   `Processing → Options → Providers → SAGA / SAGA Next Gen → Enable`.

3. Adjust the configuration section at the top of
   `qgis_dtm_plot_metrics_pipeline.py` to match your data
   (paths, ID field, radii, etc.).

4. In QGIS, open the **Python Console** (`Plugins → Python Console`).

5. In the console, click the **“Open Script”** button, select
   `qgis_dtm_plot_metrics_pipeline.py`, and then click **“Run script”**
   (the green “play” button).

6. The script will:

   - read the plot layer and mask buffer,
   - optionally align & clip all DTMs to each plot,
   - compute all configured metrics for each DTM and plot, and
   - write the outputs under `OUT_BASE`.

7. Monitor progress in the Python Console; the script prints the DTM being
   processed and each plot/metric it writes.

---

## Notes

- The script is designed to be run as a single batch job. If you want to run only part of the pipeline (e.g. only VRM), you can comment out the corresponding `run_*` calls at the bottom of the script.
- All processing uses the QGIS Processing framework, so results and parameter choices are transparent and reproducible.
- For large numbers of DTMs or plots, consider running QGIS with sufficient RAM and disk space, as SAGA and GDAL temporary files can be sizeable.
