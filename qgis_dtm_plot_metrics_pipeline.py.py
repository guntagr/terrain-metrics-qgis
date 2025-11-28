"""
QGIS Python Console script

One-pass workflow to:
- Clip DTMs by plot polygons,
- Align DTMs to a reference raster, and
- Compute per-plot terrain metrics with configurable output filenames.

Configure the paths and options in the CONFIG section before running.
"""

import os
import glob
import math
import uuid
import re
import processing

from qgis.core import (
    QgsApplication,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsFeatureRequest,
    QgsCoordinateTransform,
    QgsProject,
)
from osgeo import gdal


# =============================================================================
# CONFIGURATION
# =============================================================================

# Root folder containing all DTM rasters that will be:
# 1) used for metric extraction, and
# 2) clipped per plot.
DTM_ROOT = r"/path/to/dtm_root_folder"

# If you have a separate folder for the *source* DTMs (before alignment),
# set it here. Otherwise, keep it equal to DTM_ROOT.
SRC_DTM_ROOT = DTM_ROOT

# Plot polygons (e.g. 30x30 m plots) used to clip DTMs.
MASK_SHAPE = r"/path/to/vector/plots.shp"

# Field name in MASK_SHAPE that uniquely identifies each plot.
ID_FIELD = "OBJECTID"

# Reference DTM used for alignment (snap/warp all other DTMs to this grid).
REF_PATH = r"/path/to/reference_dtm/file.tif"

# Base output folder where all derived rasters and metrics will be written.
OUT_BASE = r"/path/to/output"

# List of radii (in metres) for circular neighbourhood metrics.
RADII_M = [2.0, 4.0, 6.0]

# Channel density (e.g. number of channels for some metrics; adjust to your use case).
CHANNEL_DENSITY = 5


# -----------------------------------------------------------------------------
# Filename configuration
# -----------------------------------------------------------------------------
# Controls how output files are named.
#
# FILENAME_MODE:
#   "DTM"    -> names are prefixed with DTM_PREFIX (e.g. "UAV_LiDAR_...")
#   "SENSOR" -> names are prefixed with SENSOR_TAG (e.g. "ALS_...")
#
# DTM_PREFIX:
#   String used when FILENAME_MODE == "DTM".
#
# SENSOR_TAG:
#   String used when FILENAME_MODE == "SENSOR".
#
# RES_TAG:
#   Optional resolution tag used in filenames (e.g. "1m", "2m").
FILENAME_MODE = "DTM"   # "DTM" or "SENSOR"
DTM_PREFIX    = "UAV_LiDAR"
SENSOR_TAG    = "ALS"
RES_TAG       = "1m"


# ======= HELPERS =======
def ensure_dir(p):
    os.makedirs(p, exist_ok=True); return p

def plot_tag(pid): return f"Plot{int(pid)}"

def dtm_density_tag(dtm_stem: str) -> str:
    # matches e.g. mean_0p5m_1_fill_aligned / mean_0p5m_27_fill__aligned
    m = re.search(r"mean_1m_(\d+)", dtm_stem)
    return m.group(1) if m else dtm_stem

def make_name(metric, pid, dtm_stem=None, radius=None, polarity=None):
    """
    DTM mode examples:
      DTM  -> UAV_LiDAR_27_DTM_50cm_Plot1.tif
      TRI  -> UAV_LiDAR_27_TRI_R4_50cm_Plot1.tif
      OPEN -> UAV_LiDAR_27_OPEN_NEG_R2_50cm_Plot1.tif
    """
    m = metric.upper()
    if FILENAME_MODE.upper() == "DTM":
        dens = dtm_density_tag(dtm_stem or "DTM")
        base = f"{DTM_PREFIX}_{dens}" if DTM_PREFIX else dens
        parts = [base]
    else:
        parts = [SENSOR_TAG] if m != "DTM" else ["DTM", SENSOR_TAG]

    if m == "DTM":
        parts.append("DTM")
    elif m == "OPEN":
        parts.append(f"OPEN_{(polarity or 'POS').upper()}")
    else:
        parts.append(m)

    if radius is not None:
        parts.append(f"R{int(radius)}")

    parts += [RES_TAG, plot_tag(pid)]
    return "_".join(parts) + ".tif"

def ref_grid(path):
    ds = gdal.Open(path); assert ds
    gt = ds.GetGeoTransform()
    ox, oy, rx, ry = gt[0], gt[3], gt[1], abs(gt[5])
    ds = None
    ref_rl = QgsRasterLayer(path, "ref"); assert ref_rl.isValid()
    return ox, oy, rx, ry, ref_rl.crs()

def snap_to_grid(ext, ox, oy, rx, ry):
    xmin = ox + math.floor((ext.xMinimum() - ox) / rx) * rx
    xmax = ox + math.ceil( (ext.xMaximum() - ox) / rx) * rx
    ymax = oy - math.floor((oy - ext.yMaximum()) / ry) * ry
    ymin = oy - math.ceil( (oy - ext.yMinimum()) / ry) * ry
    return f"{xmin},{xmax},{ymin},{ymax}"

def saga_find(name_substr):
    s = name_substr.lower()
    for a in QgsApplication.processingRegistry().algorithms():
        if a.provider().id().lower() in ("sagang","saga") and s in a.displayName().lower():
            return a.id()
    raise RuntimeError(f"SAGA algorithm not found: {name_substr}")

def align_clip_to_plot(r_in, sel_mask, ref_crs, ox, oy, rx, ry, out_path, tmp_dir):
    ensure_dir(os.path.dirname(out_path))

    rin_rl = QgsRasterLayer(r_in, "rin"); assert rin_rl.isValid(), f"Unreadable input raster: {r_in}"
    rin_crs = rin_rl.crs()

    # Reproject mask to raster CRS
    mask_proj = processing.run("native:reprojectlayer", {
        "INPUT": sel_mask, "TARGET_CRS": rin_crs, "OPERATION": "", "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]

    # Physical clip
    clip_path = os.path.join(tmp_dir, f"clip_{uuid.uuid4().hex}.tif")
    processing.run("gdal:cliprasterbymasklayer", {
        "INPUT": r_in, "MASK": mask_proj,
        "CROP_TO_CUTLINE": True, "KEEP_RESOLUTION": True, "SET_RESOLUTION": False,
        "ALPHA_BAND": False, "NODATA": None,
        "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER",
        "OUTPUT": clip_path
    })
    src_rl = QgsRasterLayer(clip_path, "src")
    if not src_rl.isValid():
        clip_path2 = os.path.join(tmp_dir, f"clip2_{uuid.uuid4().hex}.tif")
        processing.run("native:cliprasterbymasklayer", {
            "INPUT": r_in, "MASK": mask_proj,
            "CROP_TO_CUTLINE": True, "KEEP_RESOLUTION": True, "OUTPUT": clip_path2
        })
        src_rl = QgsRasterLayer(clip_path2, "src2"); assert src_rl.isValid(), "Clip failed"
        clip_path = clip_path2

    # Target extent from mask geometry in ref CRS, snapped to grid
    te = snap_to_grid(
        QgsCoordinateTransform(mask_proj.crs(), ref_crs, QgsProject.instance()).transformBoundingBox(mask_proj.extent()),
        ox, oy, rx, ry
    )

    # Warp to reference grid
    processing.run("gdal:warpreproject", {
        "INPUT": clip_path, "SOURCE_CRS": src_rl.crs(), "TARGET_CRS": ref_crs,
        "RESAMPLING": 1, "NODATA": None,
        "TARGET_RESOLUTION": rx, "TARGET_EXTENT": te, "TARGET_EXTENT_CRS": ref_crs,
        "MULTITHREADING": True, "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER",
        "DATA_TYPE": 0, "OUTPUT": out_path
    })
    return out_path

# ======= DTM CLIP + ALIGN (into 1_DTM/<Plot>) =======
def run_DTM_CLIP_ALIGN(src_root, plots, buf_lyr, ref_crs, ox, oy, rx, ry, tmp_dir):
    out_root = ensure_dir(os.path.join(OUT_BASE, "1_DTM"))
    dtms = sorted(glob.glob(os.path.join(src_root, "*.tif")) + glob.glob(os.path.join(src_root, "*.asc")))
    for dtm in dtms:
        dtm_stem = os.path.splitext(os.path.basename(dtm))[0]
        for f in plots.getFeatures(QgsFeatureRequest()):
            pid = f[ID_FIELD]
            if pid is None: continue
            plot = f"Plot{pid}"
            ensure_dir(os.path.join(out_root, plot))
            sel = processing.run("native:extractbyattribute", {
                "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
            })["OUTPUT"]
            out = os.path.join(out_root, plot, make_name("DTM", pid, dtm_stem))
            print("[DTM]", align_clip_to_plot(dtm, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

# ======= METRICS =======
def run_AR(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, tmp_dir):
    out_root = ensure_dir(os.path.join(OUT_BASE, "3_AR_by_plots_aligned"))
    rsa_tmp = processing.run("sagang:realsurfacearea", {
        "DEM": dtm, "AREA": "TEMPORARY_OUTPUT",
        "distance_units": 0, "area_units": 0, "ellipsoid": "EPSG:7030"
    })["AREA"]
    for f in plots.getFeatures(QgsFeatureRequest()):
        pid = f[ID_FIELD]; 
        if pid is None: continue
        plot = f"Plot{pid}"; ensure_dir(os.path.join(out_root, plot))
        sel = processing.run("native:extractbyattribute", {
            "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
        })["OUTPUT"]
        out = os.path.join(out_root, plot, make_name("AR", pid, dtm_stem))
        print("[AR]", align_clip_to_plot(rsa_tmp, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

def run_ROUGHNESS(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, tmp_dir):
    out_root = ensure_dir(os.path.join(OUT_BASE, "2_ROUGHNESS_by_plots_aligned"))
    rough = processing.run("gdal:roughness", {
        "INPUT": dtm, "BAND": 1, "COMPUTE_EDGES": True,
        "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]
    for f in plots.getFeatures(QgsFeatureRequest()):
        pid = f[ID_FIELD]; 
        if pid is None: continue
        plot = f"Plot{pid}"; ensure_dir(os.path.join(out_root, plot))
        sel = processing.run("native:extractbyattribute", {
            "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
        })["OUTPUT"]
        out = os.path.join(out_root, plot, make_name("ROUGHNESS", pid, dtm_stem))
        print("[ROUGHNESS]", align_clip_to_plot(rough, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

def run_OPENNESS(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, radii_m, tmp_dir):
    ALG_OPEN = saga_find("topographic openness")
    def openness_full(dtm_path, R):
        alg = QgsApplication.processingRegistry().algorithmById(ALG_OPEN)
        def pick(keys, outs=False):
            names = [d.name() for d in (alg.outputDefinitions() if outs else alg.parameterDefinitions())]
            for k in keys:
                for n in names:
                    if n.upper()==k.upper(): return n
            return None
        dem_key = pick(["DEM","ELEVATION","GRID"])
        rad_key = pick(["RADIUS","RADIALLIMIT","RADIUS_MAX"])
        pos_key = pick(["POS","POSITIVE"], outs=True)
        neg_key = pick(["NEG","NEGATIVE"], outs=True)
        res = processing.run(ALG_OPEN, {
            dem_key: dtm_path, rad_key: float(R),
            "DIRS":0, "DIR":315.0, "NSECT":8, "METHOD":1, "MSCALE":3.0, "UNIT":0, "NDIFF":True,
            pos_key:"TEMPORARY_OUTPUT", neg_key:"TEMPORARY_OUTPUT"
        })
        return {"pos": res[pos_key], "neg": res[neg_key]}
    dist_cells = int(math.ceil(max(radii_m)/rx))
    filled = processing.run("gdal:fillnodata", {
        "INPUT": dtm, "BAND": 1, "DISTANCE": dist_cells, "ITERATIONS": 1,
        "NO_MASK": False, "MASK": None,
        "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER", "EXTRA": "",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]
    out_root = ensure_dir(os.path.join(OUT_BASE, "4_OPENNESS_by_plots_aligned"))
    for R in radii_m:
        full = openness_full(filled, R)
        for f in plots.getFeatures(QgsFeatureRequest()):
            pid = f[ID_FIELD]; 
            if pid is None: continue
            plot = f"Plot{pid}"
            sel = processing.run("native:extractbyattribute", {
                "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
            })["OUTPUT"]
            for key, sub in (("pos","pos"), ("neg","neg")):
                ensure_dir(os.path.join(out_root, plot, f"R{int(R)}", sub))
                out = os.path.join(out_root, plot, f"R{int(R)}", sub,
                                   make_name("OPEN", pid, dtm_stem, radius=R, polarity=("POS" if key=="pos" else "NEG")))
                print("[OPEN]", align_clip_to_plot(full[key], sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

def run_TRI(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, radii_m, tmp_dir):
    ALG_TRI = saga_find("terrain ruggedness index")
    SEARCH_MODE = 1; WEIGHT_FUNC = 0; POWER = 2.0; BANDWIDTH = 75.0
    def tri_full(dtm_path, R):
        alg = QgsApplication.processingRegistry().algorithmById(ALG_TRI)
        def pick(keys, outs=False):
            names = [d.name() for d in (alg.outputDefinitions() if outs else alg.parameterDefinitions())]
            for k in keys:
                for n in names:
                    if n.upper()==k.upper(): return n
            return None
        dem_key = pick(["DEM","ELEVATION","GRID"])
        rad_key = pick(["RADIUS","SEARCH_RADIUS","R"])
        mode_key = pick(["MODE","SEARCH_MODE"])
        wfun_key = pick(["DW_WEIGHTING","WEIGHTING","WEIGHT_FUNC"])
        pow_key  = pick(["DW_POWER","POWER"])
        bw_key   = pick(["DW_BANDWIDTH","BANDWIDTH"])
        out_key  = pick(["TRI","RUGGEDNESS","RESULT"], outs=True)
        res = processing.run(ALG_TRI, {
            dem_key: dtm_path, rad_key: float(R),
            (mode_key or "MODE"): SEARCH_MODE,
            (wfun_key or "DW_WEIGHTING"): WEIGHT_FUNC,
            (pow_key  or "DW_POWER"): POWER,
            (bw_key   or "DW_BANDWIDTH"): BANDWIDTH,
            out_key: "TEMPORARY_OUTPUT"
        })
        return res[out_key]
    dist_cells = int(math.ceil(max(radii_m)/rx))
    filled = processing.run("gdal:fillnodata", {
        "INPUT": dtm, "BAND": 1, "DISTANCE": dist_cells, "ITERATIONS": 1,
        "NO_MASK": False, "MASK": None,
        "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER", "EXTRA": "",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]
    out_root = ensure_dir(os.path.join(OUT_BASE, "5_TRI_by_plots_aligned"))
    for R in radii_m:
        tri_r = tri_full(filled, R)
        for f in plots.getFeatures(QgsFeatureRequest()):
            pid = f[ID_FIELD]; 
            if pid is None: continue
            plot = f"Plot{pid}"; ensure_dir(os.path.join(out_root, plot, f"R{int(R)}"))
            sel = processing.run("native:extractbyattribute", {
                "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
            })["OUTPUT"]
            out = os.path.join(out_root, plot, f"R{int(R)}", make_name("TRI", pid, dtm_stem, radius=R))
            print("[TRI]", align_clip_to_plot(tri_r, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

def run_VRM(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, radii_m, tmp_dir):
    ALG_VRM = saga_find("vector ruggedness measure")
    SEARCH_MODE = 1; WEIGHT_FUNC = 0; POWER = 2.0; BANDWIDTH = 75.0
    def vrm_full(dtm_path, R):
        alg = QgsApplication.processingRegistry().algorithmById(ALG_VRM)
        def pick(keys, outs=False):
            names = [d.name() for d in (alg.outputDefinitions() if outs else alg.parameterDefinitions())]
            for k in keys:
                for n in names:
                    if n.upper()==k.upper(): return n
            return None
        dem_key = pick(["DEM","ELEVATION","GRID"])
        rad_key = pick(["RADIUS","SEARCH_RADIUS","R"])
        mode_key = pick(["MODE","SEARCH_MODE"])
        wfun_key = pick(["DW_WEIGHTING","WEIGHTING","WEIGHT_FUNC"])
        pow_key  = pick(["DW_POWER","POWER"])
        bw_key   = pick(["DW_BANDWIDTH","BANDWIDTH"])
        out_key  = pick(["VRM","VECTOR_TERRAIN_RUGGEDNESS","RESULT"], outs=True)
        res = processing.run(ALG_VRM, {
            dem_key: dtm_path, rad_key: float(R),
            (mode_key or "MODE"): SEARCH_MODE,
            (wfun_key or "DW_WEIGHTING"): WEIGHT_FUNC,
            (pow_key  or "DW_POWER"): POWER,
            (bw_key   or "DW_BANDWIDTH"): BANDWIDTH,
            out_key: "TEMPORARY_OUTPUT"
        })
        return res[out_key]
    dist_cells = int(math.ceil(max(radii_m)/rx))
    filled = processing.run("gdal:fillnodata", {
        "INPUT": dtm, "BAND": 1, "DISTANCE": dist_cells, "ITERATIONS": 1,
        "NO_MASK": False, "MASK": None,
        "OPTIONS": "TILED=YES,COMPRESS=LZW,BIGTIFF=IF_SAFER", "EXTRA": "",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]
    out_root = ensure_dir(os.path.join(OUT_BASE, "6_VRM_by_plots_aligned"))
    for R in radii_m:
        vrm_r = vrm_full(filled, R)
        for f in plots.getFeatures(QgsFeatureRequest()):
            pid = f[ID_FIELD]; 
            if pid is None: continue
            plot = f"Plot{pid}"; ensure_dir(os.path.join(out_root, plot, f"R{int(R)}"))
            sel = processing.run("native:extractbyattribute", {
                "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
            })["OUTPUT"]
            out = os.path.join(out_root, plot, f"R{int(R)}", make_name("VRM", pid, dtm_stem, radius=R))
            print("[VRM]", align_clip_to_plot(vrm_r, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

def run_BASIC_TERRAIN(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, tmp_dir):
    ALG_BASIC = saga_find("basic terrain analysis")
    alg = QgsApplication.processingRegistry().algorithmById(ALG_BASIC)
    elev_key = next(p.name() for p in alg.parameterDefinitions() if p.name().upper() in ("ELEVATION","DEM","GRID"))
    dens_key = next((p.name() for p in alg.parameterDefinitions() if "DENSITY" in p.name().upper()), None)
    tmp_bt = ensure_dir(os.path.join(OUT_BASE, "_tmp_basic"))
    params = {elev_key: dtm}
    if dens_key: params[dens_key] = CHANNEL_DENSITY
    bases = {}
    for od in alg.outputDefinitions():
        name = od.name()
        base = os.path.join(tmp_bt, f"{dtm_stem}__{name}")
        params[name] = base
        bases[name] = base
    processing.run(ALG_BASIC, params)
    rasters = {name: base + ".sdat" for name, base in bases.items() if os.path.exists(base + ".sdat")}
    out_root = ensure_dir(os.path.join(OUT_BASE, "7_BASIC_TERRAIN_by_plots_aligned"))
    for f in plots.getFeatures(QgsFeatureRequest()):
        pid = f[ID_FIELD]; 
        if pid is None: continue
        plot = f"Plot{pid}"; ensure_dir(os.path.join(out_root, plot))
        sel = processing.run("native:extractbyattribute", {
            "INPUT": buf_lyr, "FIELD": ID_FIELD, "OPERATOR": 0, "VALUE": pid, "OUTPUT": "TEMPORARY_OUTPUT"
        })["OUTPUT"]
        for metric, sdat in rasters.items():
            ensure_dir(os.path.join(out_root, plot, metric))
            out = os.path.join(out_root, plot, metric, make_name(metric, pid, dtm_stem))
            print("[BASIC]", align_clip_to_plot(sdat, sel, ref_crs, ox, oy, rx, ry, out, tmp_dir))

# ======= SETUP =======
ensure_dir(OUT_BASE)
ox, oy, rx, ry, ref_crs = ref_grid(REF_PATH)
BUFFER_M = rx
plots = QgsVectorLayer(MASK_SHAPE, "plots", "ogr")
assert plots.isValid() and ID_FIELD in [f.name() for f in plots.fields()]
buf_lyr = processing.run("native:buffer", {
    "INPUT": plots, "DISTANCE": float(BUFFER_M),
    "SEGMENTS": 8, "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
    "DISSOLVE": False, "SEPARATE_DISJOINT": False, "OUTPUT": "TEMPORARY_OUTPUT"
})["OUTPUT"]
TMP_DIR = ensure_dir(os.path.join(OUT_BASE, "_tmp"))

# ======= MAIN =======
# 1) Clip+align source DTMs into 1_DTM/<Plot>/DTM_* files
run_DTM_CLIP_ALIGN(SRC_DTM_ROOT, plots, buf_lyr, ref_crs, ox, oy, rx, ry, TMP_DIR)

# 2) Run metrics on DTM_ROOT
dtm_list = sorted(glob.glob(os.path.join(DTM_ROOT, "*.tif")) + glob.glob(os.path.join(DTM_ROOT, "*.asc")))
for dtm in dtm_list:
    dtm_stem = os.path.splitext(os.path.basename(dtm))[0]
    print("\n=== DTM:", dtm_stem, "===")
    run_AR(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, TMP_DIR)
    run_ROUGHNESS(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, TMP_DIR)
    run_OPENNESS(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, RADII_M, TMP_DIR)
    run_TRI(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, RADII_M, TMP_DIR)
    run_VRM(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, RADII_M, TMP_DIR)
    run_BASIC_TERRAIN(dtm, dtm_stem, plots, buf_lyr, ref_crs, ox, oy, rx, ry, TMP_DIR)

print("\nDone.")
