#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
6UTIL_plot_all_trajectories.py

Plots all real-world + simulated drive trajectories against the scenario
segment on an OSM background, and prints a LaTeX completion-rate table.

Reads from (project root):
    data/processed_dataset/<BAG>/maps/map.xodr
    data/data_for_validation/real_world_trajectories/scenario_segment.json
    data/data_for_validation/real_world_trajectories/trajectory<N>.csv
    data/data_for_validation/GS_trajectories/splatfacto_run<N>_trajectory.json

Writes to:
    interactive matplotlib figure
    LaTeX table printed to stdout
"""

import os
import re
import json
import sys
import numpy as np
import matplotlib.pyplot as plt
import contextily as ctx
from pyproj import Transformer


# =============================================================================
#  PATH SETUP
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

BAG_NAME = "reference_bag"

MAP_XODR = os.path.join(
    PROJECT_ROOT, "data", "processed_dataset", BAG_NAME, "maps", "map.xodr"
)

RW_DIR = os.path.join(
    PROJECT_ROOT, "data", "data_for_validation", "real_world_trajectories"
)
SEGMENT_JSON = os.path.join(RW_DIR, "scenario_segment.json")

SIM_DIR = os.path.join(
    PROJECT_ROOT, "data", "data_for_validation", "GS_trajectories"
)

# Plot config
MAP_BUFFER_M = 100
FAIL_THRESHOLD_PCT = 95.0

RW_COLOR = "#1E88E5"
SIM_COLOR = "#FF9800"
LINE_STYLES = ["-", "--", ":"]


# =============================================================================
#  XODR PARSING
# =============================================================================

def get_xodr_projection_params(xodr_data):
    geo_match = re.search(
        r'<geoReference>\s*<!\[CDATA\[(.*?)\]\]>', xodr_data, re.DOTALL
    )
    geo_ref = geo_match.group(1).strip() if geo_match else "+proj=tmerc"
    offset_match = re.search(r'<offset\s+x="([^"]+)"\s+y="([^"]+)"', xodr_data)
    if offset_match:
        offset = (float(offset_match.group(1)), float(offset_match.group(2)))
    else:
        offset = (0.0, 0.0)
    return {"geo_reference": geo_ref, "offset": offset}


# =============================================================================
#  COORDINATE CONVERSIONS
# =============================================================================

def setup_transforms(xodr_path):
    with open(xodr_path, "r") as f:
        xodr_data = f.read()
    params = get_xodr_projection_params(xodr_data)
    xodr_offset = params["offset"]
    proj_string = params["geo_reference"].strip()
    if proj_string == "+proj=tmerc":
        proj_string = "+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +datum=WGS84"

    tf_proj_to_wgs = Transformer.from_crs(proj_string, "EPSG:4326", always_xy=True)
    tf_wgs_to_proj = Transformer.from_crs("EPSG:4326", proj_string, always_xy=True)
    tf_utm_to_wgs  = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    tf_wgs_to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    return tf_proj_to_wgs, tf_wgs_to_proj, tf_utm_to_wgs, tf_wgs_to_merc, xodr_offset


def carla_to_merc(carla_x, carla_y, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset):
    proj_x = carla_x - xodr_offset[0]
    proj_y = (-carla_y) - xodr_offset[1]
    lon, lat = tf_proj_to_wgs.transform(proj_x, proj_y)
    mx, my = tf_wgs_to_merc.transform(lon, lat)
    return mx, my


def carla_array_to_merc(cxs, cys, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset):
    mxs, mys = [], []
    for x, y in zip(cxs, cys):
        mx, my = carla_to_merc(x, y, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset)
        mxs.append(mx)
        mys.append(my)
    return np.array(mxs), np.array(mys)


def utm_to_merc(utm_x, utm_y, tf_utm_to_wgs, tf_wgs_to_merc):
    lon, lat = tf_utm_to_wgs.transform(utm_x, utm_y)
    mx, my = tf_wgs_to_merc.transform(lon, lat)
    return mx, my


def utm_array_to_merc(uxs, uys, tf_utm_to_wgs, tf_wgs_to_merc):
    mxs, mys = [], []
    for ux, uy in zip(uxs, uys):
        mx, my = utm_to_merc(ux, uy, tf_utm_to_wgs, tf_wgs_to_merc)
        mxs.append(mx)
        mys.append(my)
    return np.array(mxs), np.array(mys)


def utm_to_carla(utm_x, utm_y, tf_utm_to_wgs, tf_wgs_to_proj, xodr_offset):
    lon, lat = tf_utm_to_wgs.transform(utm_x, utm_y)
    proj_x, proj_y = tf_wgs_to_proj.transform(lon, lat)
    carla_x = proj_x + xodr_offset[0]
    carla_y = -(proj_y + xodr_offset[1])
    return carla_x, carla_y


def utm_array_to_carla(uxs, uys, tf_utm_to_wgs, tf_wgs_to_proj, xodr_offset):
    cxs, cys = [], []
    for ux, uy in zip(uxs, uys):
        cx, cy = utm_to_carla(ux, uy, tf_utm_to_wgs, tf_wgs_to_proj, xodr_offset)
        cxs.append(cx)
        cys.append(cy)
    return np.array(cxs), np.array(cys)


# =============================================================================
#  PROJECTION ONTO REFERENCE PATH
# =============================================================================

def project_onto_reference(traj_xs, traj_ys, ref_xs, ref_ys, ref_s):
    """Project trajectory onto reference path -> 1D progress (arc length)."""
    progress = np.zeros(len(traj_xs))
    for i in range(len(traj_xs)):
        dists = np.sqrt((ref_xs - traj_xs[i]) ** 2 + (ref_ys - traj_ys[i]) ** 2)
        closest_idx = np.argmin(dists)
        progress[i] = ref_s[closest_idx]

    # Force monotonic increase so a quick backtrack doesn't lower completion.
    for i in range(1, len(progress)):
        if progress[i] < progress[i - 1]:
            progress[i] = progress[i - 1]
    return progress


# =============================================================================
#  LOADERS
# =============================================================================

def load_real_trajectory_csv(path):
    """Load real-world trajectory CSV (timestamp,x,y,z,yaw) -> (utm_x, utm_y)."""
    xs, ys = [], []
    with open(path, "r") as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                xs.append(float(parts[1]))
                ys.append(float(parts[2]))
            except ValueError:
                continue
    return np.array(xs), np.array(ys)


def load_sim_trajectory(path):
    """Load simulated trajectory JSON (list of dicts with x, y) -> (carla_x, carla_y)."""
    with open(path, "r") as f:
        data = json.load(f)
    xs = np.array([p["x"] for p in data])
    ys = np.array([p["y"] for p in data])
    return xs, ys


# =============================================================================
#  LATEX TABLE
# =============================================================================

def generate_latex_table(completions_rw, completions_sim, fail_threshold):
    """
    Simple LaTeX table: Real vs Sim, single condition.
    completions_rw: dict run -> completion %
    completions_sim: dict run -> completion %
    """

    def fail_rate(d):
        if not d:
            return "---"
        n_total = len(d)
        n_failed = sum(1 for v in d.values() if v < fail_threshold)
        return f"{n_failed}/{n_total}"

    def completion_avg_max_min(d):
        if not d:
            return "---"
        vals = list(d.values())
        return f"{np.mean(vals):.0f} - {np.max(vals):.0f} - {np.min(vals):.0f}"

    lines = []
    lines.append(r"\begin{table}[H]")
    lines.append(r"\centering")
    lines.append(r"\caption{System-level evaluation: real-world vs simulated drive runs.}")
    lines.append(r"\label{tab:system_eval}")
    lines.append(r"\begin{tabular}{llc}")
    lines.append(r"\hline")
    lines.append(r"\textbf{Metric} & \textbf{Domain} & \textbf{Value} \\")
    lines.append(r"\hline")

    # Fail rate
    lines.append(f"Fail Rate & Real & {fail_rate(completions_rw)} " + r"\\")
    lines.append(f"          & 3DGS & {fail_rate(completions_sim)} " + r"\\")

    # Completion
    lines.append(
        r"\makecell[l]{Completion Rate (\%)\\\scriptsize{avg--max--min}} "
        f"& Real & {completion_avg_max_min(completions_rw)} " + r"\\"
    )
    lines.append(
        f"          & 3DGS & {completion_avg_max_min(completions_sim)} " + r"\\"
    )

    lines.append(r"\hline")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


# =============================================================================
#  MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("PLOT ALL TRAJECTORIES (real-world + simulated)")
    print("=" * 80)
    print(f"[INFO] Project root:    {PROJECT_ROOT}")
    print(f"[INFO] Map XODR:        {MAP_XODR}")
    print(f"[INFO] Segment JSON:    {SEGMENT_JSON}")
    print(f"[INFO] Real-world dir:  {RW_DIR}")
    print(f"[INFO] Simulated dir:   {SIM_DIR}")
    print("=" * 80)

    # ---- Validate inputs ----
    for required in [MAP_XODR, SEGMENT_JSON]:
        if not os.path.exists(required):
            print(f"[ERROR] Missing file: {required}")
            sys.exit(1)
    if not os.path.isdir(RW_DIR):
        print(f"[ERROR] Missing dir: {RW_DIR}")
        sys.exit(1)
    if not os.path.isdir(SIM_DIR):
        print(f"[ERROR] Missing dir: {SIM_DIR}")
        sys.exit(1)

    # ---- Setup transforms ----
    tf_proj_to_wgs, tf_wgs_to_proj, tf_utm_to_wgs, tf_wgs_to_merc, xodr_offset = \
        setup_transforms(MAP_XODR)

    # ---- Load scenario segment (reference path in CARLA coords) ----
    with open(SEGMENT_JSON, "r") as f:
        segment = json.load(f)

    ref_carla_x = np.array(segment["reference_path_carla_x"])
    ref_carla_y = np.array(segment["reference_path_carla_y"])
    ref_s = np.array(segment["reference_path_arc_length"])
    seg_start = segment["scenario_start_m"]
    seg_end = segment["scenario_end_m"]
    seg_length = segment["scenario_length_m"]

    seg_mask = (ref_s >= seg_start) & (ref_s <= seg_end)
    seg_cx = ref_carla_x[seg_mask]
    seg_cy = ref_carla_y[seg_mask]

    seg_mx, seg_my = carla_array_to_merc(
        seg_cx, seg_cy, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset
    )
    ref_mx, ref_my = carla_array_to_merc(
        ref_carla_x, ref_carla_y, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset
    )

    print(f"[INFO] Scenario segment: {seg_length:.1f} m "
          f"[{seg_start:.1f} -> {seg_end:.1f}]")

    completions_rw = {}
    completions_sim = {}

    # ---- Load real-world trajectories: trajectory<N>.csv ----
    print("\nLoading real-world trajectories...")
    rw_trajs = {}
    for fname in sorted(os.listdir(RW_DIR)):
        match = re.match(r"trajectory(\d+)\.csv$", fname)
        if not match:
            continue
        run = int(match.group(1))
        path = os.path.join(RW_DIR, fname)
        utm_x, utm_y = load_real_trajectory_csv(path)
        if len(utm_x) == 0:
            print(f"  [WARN] {fname}: empty trajectory, skipping")
            continue
        mx, my = utm_array_to_merc(utm_x, utm_y, tf_utm_to_wgs, tf_wgs_to_merc)
        carla_x, carla_y = utm_array_to_carla(
            utm_x, utm_y, tf_utm_to_wgs, tf_wgs_to_proj, xodr_offset
        )
        progress = project_onto_reference(
            carla_x, carla_y, ref_carla_x, ref_carla_y, ref_s
        )
        rw_trajs[run] = (mx, my)

        completion = max(0.0, min(1.0, (progress[-1] - seg_start) / seg_length)) * 100
        completions_rw[run] = completion
        print(f"  {fname}: {len(mx)} pts, progress "
              f"[{progress[0]:.1f} -> {progress[-1]:.1f}] m, "
              f"completion={completion:.1f}%")

    # ---- Load simulated trajectories: splatfacto_run<N>_trajectory.json ----
    print("\nLoading simulated trajectories...")
    sim_trajs = {}
    for fname in sorted(os.listdir(SIM_DIR)):
        match = re.match(r"splatfacto_run(\d+)_trajectory\.json$", fname)
        if not match:
            continue
        run = int(match.group(1))
        path = os.path.join(SIM_DIR, fname)
        sim_x, sim_y = load_sim_trajectory(path)
        if len(sim_x) == 0:
            print(f"  [WARN] {fname}: empty trajectory, skipping")
            continue
        mx, my = carla_array_to_merc(
            sim_x, sim_y, tf_proj_to_wgs, tf_wgs_to_merc, xodr_offset
        )
        progress = project_onto_reference(
            sim_x, sim_y, ref_carla_x, ref_carla_y, ref_s
        )
        sim_trajs[run] = (mx, my)

        completion = max(0.0, min(1.0, (progress[-1] - seg_start) / seg_length)) * 100
        completions_sim[run] = completion
        print(f"  {fname}: {len(mx)} pts, progress "
              f"[{progress[0]:.1f} -> {progress[-1]:.1f}] m, "
              f"completion={completion:.1f}%")

    if not rw_trajs and not sim_trajs:
        print("[ERROR] No trajectories loaded, nothing to plot.")
        sys.exit(1)

    # ---- Compute global bounds ----
    all_mx_list = [ref_mx]
    all_my_list = [ref_my]
    for mx, my in rw_trajs.values():
        all_mx_list.append(mx)
        all_my_list.append(my)
    for mx, my in sim_trajs.values():
        all_mx_list.append(mx)
        all_my_list.append(my)
    all_mx_cat = np.concatenate(all_mx_list)
    all_my_cat = np.concatenate(all_my_list)
    xmin, xmax = all_mx_cat.min() - MAP_BUFFER_M, all_mx_cat.max() + MAP_BUFFER_M
    ymin, ymax = all_my_cat.min() - MAP_BUFFER_M, all_my_cat.max() + MAP_BUFFER_M

    # ---- Plot ----
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    try:
        ctx.add_basemap(ax, crs="EPSG:3857", source=ctx.providers.CartoDB.Positron)
    except Exception as e:
        print(f"  [WARN] Could not load OSM tiles: {e}")

    # Scenario segment as thick light-green band
    ax.plot(seg_mx, seg_my, color="#4CAF50", linewidth=8, alpha=0.25,
            solid_capstyle="round", zorder=5)
    ax.plot(seg_mx[0], seg_my[0], marker="|", color="#4CAF50",
            markersize=20, markeredgewidth=4, zorder=25)
    ax.plot(seg_mx[-1], seg_my[-1], marker="|", color="#F44336",
            markersize=20, markeredgewidth=4, zorder=25)

    # Real-world runs
    for run in sorted(rw_trajs.keys()):
        mx, my = rw_trajs[run]
        ls = LINE_STYLES[(run - 1) % len(LINE_STYLES)]
        ax.plot(mx, my, color=RW_COLOR, linewidth=2.0, alpha=0.8,
                linestyle=ls, zorder=10, label=f"Real run{run}")
        ax.plot(mx[0],  my[0],  marker="o", color=RW_COLOR, markersize=6,
                markeredgecolor="black", markeredgewidth=1, zorder=20)
        ax.plot(mx[-1], my[-1], marker="s", color=RW_COLOR, markersize=6,
                markeredgecolor="black", markeredgewidth=1, zorder=20)

    # Simulated runs
    for run in sorted(sim_trajs.keys()):
        mx, my = sim_trajs[run]
        ls = LINE_STYLES[(run - 1) % len(LINE_STYLES)]
        ax.plot(mx, my, color=SIM_COLOR, linewidth=1.8, alpha=0.8,
                linestyle=ls, zorder=12, label=f"3DGS run{run}")
        ax.plot(mx[0],  my[0],  marker="o", color=SIM_COLOR, markersize=6,
                markeredgecolor="black", markeredgewidth=1, zorder=20)
        ax.plot(mx[-1], my[-1], marker="x", color=SIM_COLOR, markersize=10,
                markeredgewidth=2.5, zorder=20)

    ax.set_title(
        f"Real-world vs simulated trajectories  "
        f"(scenario segment: {seg_length:.0f} m)",
        fontsize=14,
    )
    ax.legend(loc="upper right", fontsize=9)
    ax.set_axis_off()

    plt.tight_layout()
    plt.show()

    # ---- LaTeX table ----
    print("\n" + "=" * 70)
    print("  LATEX TABLE")
    print("=" * 70)
    latex = generate_latex_table(
        completions_rw, completions_sim, fail_threshold=FAIL_THRESHOLD_PCT
    )
    print(latex)
    print("\nDone.")


if __name__ == "__main__":
    main()