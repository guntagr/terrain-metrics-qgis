# QGIS DTM Metrics Extractor

This repository contains a QGIS Python script that clips, aligns, and computes terrain metrics from UAV and HLS LiDAR-derived DTMs at multiple resolutions (0.5 m, 1 m, 2 m).  
It uses QGIS Processing tools with **GDAL** and **SAGA GIS** algorithms.

## Features
- One-pass DTM clip + align to plot boundaries
- Outputs aligned per-plot rasters with consistent naming
- Computes terrain metrics:
  - Surface Area Ratio (SAGA: Real Surface Area)
  - Roughness (GDAL: Roughness)
  - Openness (SAGA: Topographic Openness)
  - Terrain Ruggedness Index (SAGA: TRI)
  - Vector Ruggedness Measure (SAGA: VRM)
  - Basic Terrain Analysis (SAGA: slope, aspect, curvature, TWI, etc.)

## Requirements
- QGIS with Processing Toolbox
- GDAL
- SAGA GIS installed and accessible from QGIS

## Configuration

The script is controlled by a few key variables at the top:

- `MASK_SHAPE` – Path to the shapefile of plot polygons (default: `plots_30x30m_squares.shp`).  
- `ID_FIELD` – Attribute field used as plot ID (default: `OBJECTID`).  
- `RUNS` – List of datasets with:
  - `REF` – Reference DTM for grid alignment
  - `DTM_ROOT` – Directory of input DTMs
  - `RES_TAG` – Resolution tag used in output filenames
  - `OUT_SUFFIX` – Output folder suffix

- `RADII_M` – Window radii (in meters) for TRI, VRM, and Openness metrics. Default: `[2.0, 4.0, 6.0]`.  
  Adjust this list to change the scale of analysis.  

- `CHANNEL_DENSITY` – Parameter for SAGA “Basic Terrain Analysis”. Controls drainage network density.  

## Usage
1. Place your plot shapefile and DTM datasets in the configured folders.
2. Adjust `MASK_SHAPE`, `ID_FIELD`, and `RUNS` paths in the script.
3. Run the script inside the QGIS Python Console.
4. Outputs are written to subfolders in the dataset directories.

## Output Naming
Example outputs:
- `UAV_LiDAR_5_DTM_50cm_Plot1.tif`
- `UAV_LiDAR_5_ROUGHNESS_50cm_Plot1.tif`
- `UAV_LiDAR_5_OPEN_NEG_R2_50cm_Plot1.tif`
