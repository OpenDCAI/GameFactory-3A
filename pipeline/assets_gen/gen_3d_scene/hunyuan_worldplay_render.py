"""
pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_render.py

Render a scene mesh offscreen so it can be inspected for discontinuities.

The metrics in `hunyuan_worldplay_eval.py` say how bad a mesh is; this says what
is wrong with it. Holes read as background, and the sheets that span occlusion
boundaries read as smooth panels facing away from everything around them.

Rasterising is done in numpy rather than through OpenGL: headless boxes rarely
carry libEGL or OSMesa, and `trimesh`'s own preview needs one of them. Triangles
are bucketed by bounding-box size so each bucket fills in one vectorised
barycentric pass, which keeps 450k faces well under a second per view.

Usage:
    from pipeline.assets_gen.gen_3d_scene.hunyuan_worldplay_render import render_glb
    render_glb("scene.glb", "scene.png")

    # Or from the shell, with RENDER=pipeline/assets_gen/gen_3d_scene/...
    python $RENDER scene.glb scene.png
    python $RENDER scene.glb wide.png --angles 0 30 60 90 --width 960
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Make repo root importable regardless of CWD ───────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# ──────────────────────────────────────────────────────────────────────────────

import numpy as np  # noqa: E402

BACKGROUND = np.array([18, 18, 22], dtype=np.float32)

#: Orbit angles in degrees, paired with how far the eye rises for each. Straight
#: on first, since that is the view the reconstruction was solved for.
DEFAULT_ANGLES = (0.0, 25.0, 50.0)


def load_mesh(path: str | Path):
    """Load a GLB/PLY as one concatenated `Trimesh`, flattening any scene graph."""
    import trimesh

    loaded = trimesh.load(str(path))
    if isinstance(loaded, trimesh.Scene):
        return trimesh.util.concatenate(
            [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        )
    return loaded


def mesh_report(mesh) -> dict[str, float]:
    """Shape statistics worth printing beside a render."""
    areas = mesh.area_faces
    p50, p999 = np.percentile(areas, [50, 99.9])
    return {
        "num_vertices": float(len(mesh.vertices)),
        "num_faces": float(len(mesh.faces)),
        "num_components": float(len(mesh.split(only_watertight=False))),
        "face_area_p50": float(p50),
        "face_area_p999": float(p999),
        "area_share_of_largest_0.1pct": float(areas[areas >= p999].sum() / areas.sum()),
    }


def _look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.asarray(up, dtype=np.float64))
    norm = np.linalg.norm(right)
    # Degenerate when the view direction is parallel to `up`; any perpendicular
    # will do, since roll is arbitrary for these previews.
    right = np.array([1.0, 0.0, 0.0]) if norm < 1e-9 else right / norm
    return np.stack([right, np.cross(right, forward), -forward])


def _project(vertices, eye, rotation, width, height, fov_deg):
    camera_space = (vertices - eye) @ rotation.T
    depth = -camera_space[:, 2]
    focal = 0.5 * height / np.tan(np.radians(fov_deg) / 2)
    safe = np.where(np.abs(depth) < 1e-6, 1e-6, depth)
    return (
        np.stack([
            camera_space[:, 0] / safe * focal + width / 2,
            -camera_space[:, 1] / safe * focal + height / 2,
        ], axis=-1),
        depth,
    )


def rasterise(mesh, eye, target, width: int = 720, height: int = 540,
              fov_deg: float = 50.0):
    """Render one view of `mesh` and return it as a PIL image."""
    from PIL import Image

    rotation = _look_at(eye, target)
    screen, depth = _project(mesh.vertices, eye, rotation, width, height, fov_deg)

    tri, tri_depth = screen[mesh.faces], depth[mesh.faces]
    visible = (tri_depth > 1e-4).all(axis=1)
    faces, tri, tri_depth = mesh.faces[visible], tri[visible], tri_depth[visible]

    shade = np.clip(np.abs((mesh.face_normals[visible] @ rotation.T)[:, 2]), 0.35, 1.0)
    base = np.tile(np.array([200, 205, 215], dtype=np.float32), (len(faces), 1))
    if mesh.visual.kind == "vertex":
        # Albedo makes it far easier to tell real surface from a rubber sheet.
        colours = np.asarray(mesh.visual.vertex_colors)[:, :3].astype(np.float32)
        base = colours[faces].mean(axis=1)
    colour = base * shade[:, None]

    zbuffer = np.full((height, width), np.inf)
    image = np.tile(BACKGROUND, (height, width, 1))

    low = np.floor(tri.min(axis=1)).astype(np.int64)
    span = np.clip((np.ceil(tri.max(axis=1)).astype(np.int64) - low).max(axis=1), 1, None)

    # One vectorised pass per power-of-two bounding-box size.
    bucket = np.ceil(np.log2(span)).astype(np.int64)
    for level in range(bucket.max() + 1):
        picked = np.nonzero(bucket == level)[0]
        if picked.size:
            _fill(image, zbuffer, tri[picked], tri_depth[picked], colour[picked],
                  low[picked], 1 << level, width, height)

    return Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))


def _fill(image, zbuffer, tri, tri_depth, colour, low, size, width, height):
    grid = np.arange(size + 1)
    offset_x, offset_y = np.meshgrid(grid, grid, indexing="xy")
    px = low[:, None, None, 0] + offset_x[None] + 0.5
    py = low[:, None, None, 1] + offset_y[None] + 0.5

    ax, ay = tri[:, 0, 0, None, None], tri[:, 0, 1, None, None]
    bx, by = tri[:, 1, 0, None, None], tri[:, 1, 1, None, None]
    cx, cy = tri[:, 2, 0, None, None], tri[:, 2, 1, None, None]

    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    area = np.where(np.abs(area) < 1e-12, 1e-12, area)
    w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
    w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
    w2 = 1.0 - w0 - w1

    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    inside &= (px >= 0) & (px < width) & (py >= 0) & (py < height)
    if not inside.any():
        return

    z = (w0 * tri_depth[:, 0, None, None]
         + w1 * tri_depth[:, 1, None, None]
         + w2 * tri_depth[:, 2, None, None])

    face_idx, row, col = np.nonzero(inside)
    xs = px[face_idx, row, col].astype(np.int64)
    ys = py[face_idx, row, col].astype(np.int64)
    zs = z[face_idx, row, col]

    # np.minimum.at resolves the depth race between fragments sharing a pixel;
    # a plain mask would let whichever fragment came last win.
    order = np.lexsort((zs,))
    xs, ys, zs, face_idx = xs[order], ys[order], zs[order], face_idx[order]

    winner = np.full((height, width), np.inf)
    np.minimum.at(winner, (ys, xs), zs)
    keep = (zs <= winner[ys, xs]) & (zs < zbuffer[ys, xs])
    xs, ys, zs, face_idx = xs[keep], ys[keep], zs[keep], face_idx[keep]

    zbuffer[ys, xs] = zs
    image[ys, xs] = colour[face_idx]


def render_glb(
    glb_path: str | Path,
    out_path: str | Path,
    angles=DEFAULT_ANGLES,
    width: int = 720,
    height: int = 540,
    verbose: bool = False,
) -> Path:
    """
    Render a mesh from several angles into one contact sheet.

    These meshes are depth shells seen from a camera at the origin looking down
    -Z, so the orbit is centred on what that camera saw rather than on the
    bounding box: the ground runs off toward the horizon and would otherwise drag
    the centre far behind the scene. The radius is a vertex percentile for the
    same reason, since a few stray points sit arbitrarily far away.

    Returns the path written.
    """
    from PIL import Image

    mesh = load_mesh(glb_path)
    if verbose:
        for key, value in mesh_report(mesh).items():
            print(f"       {key:32s} {value:.6g}")

    target = np.array([0.0, 0.0, np.percentile(mesh.vertices[:, 2], 35)])
    radius = float(np.percentile(np.linalg.norm(mesh.vertices - target, axis=1), 75))

    tiles = []
    for angle in angles:
        theta = np.radians(angle)
        # Rise with the swing so the far side of the shell stays readable.
        lift = 0.05 + 0.005 * abs(angle)
        direction = np.array([np.sin(theta), lift, -np.cos(theta)])
        direction /= np.linalg.norm(direction)
        tiles.append(rasterise(mesh, target - direction * radius * 1.6, target,
                               width=width, height=height))

    sheet = Image.new("RGB", (width * len(tiles), height))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (width * index, 0))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Render a scene mesh offscreen.")
    parser.add_argument("glb", help="Mesh to render (.glb / .ply)")
    parser.add_argument("out", nargs="?", default=None,
                        help="Output PNG; defaults to the mesh path with .png")
    parser.add_argument("--angles", type=float, nargs="+", default=list(DEFAULT_ANGLES),
                        help="Orbit angles in degrees, one tile each")
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args()

    out = args.out or str(Path(args.glb).with_suffix(".png"))
    print(f"[render] {args.glb}")
    written = render_glb(args.glb, out, angles=args.angles, width=args.width,
                         height=args.height, verbose=True)
    print(f"[render] {len(args.angles)} view(s) → {written}")


if __name__ == "__main__":
    main()
