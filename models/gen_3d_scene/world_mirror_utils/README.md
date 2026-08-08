# world_mirror_utils — vendored HunyuanWorld-Mirror

Network definition for [HunyuanWorld-Mirror](https://github.com/Tencent-Hunyuan/HunyuanWorld-Mirror),
the feed-forward geometry model behind Hunyuan WorldPlay's 3D reconstruction.
Weights: `tencent/HunyuanWorld-Mirror` on HuggingFace.

Copied from `HY-WorldPlay/worldcompass/reward_function/HunyuanWorldMirror/src`.
Original license in `License.txt` / `Notice.txt`.

## Local modifications

1. **Import prefix rewritten** — `reward_function.HunyuanWorldMirror.src.*` →
   `models.gen_3d_scene.world_mirror_utils.src.*`, so the tree is a normal
   subpackage and needs no `sys.path` manipulation.

2. **`gsplat` made optional** in `src/models/models/rasterization.py`. Mesh
   generation only uses the depth / pointmap / normal heads; the Gaussian branch
   now raises at call time rather than blocking the import. Pair with
   `WorldMirrorModel(enable_gs=False)`, which is the default.

3. **Unused modules deleted** to keep the dependency surface small:
   `render_utils.py` (moviepy), `color_map.py` (colorspacious, jaxtyping),
   `build_pycolmap_recon.py` (pycolmap), `gs_effects.py`, `cropping.py`,
   `save_utils.py`, `inference_utils.py`, and `visual_util.py`.

   `visual_util.py` held the upstream depth→mesh code (`create_image_mesh`,
   `convert_predictions_to_glb_scene`). It is deliberately **not** vendored —
   `operators/gen_3d_scene/funcs/points_to_mesh.py` replaces it, because the
   upstream version is what produces the holes described in that file's header.

## Refreshing from upstream

```bash
SRC=/path/to/HunyuanWorldMirror
cp -r "$SRC/src" world_mirror_utils/
find world_mirror_utils -name '*.py' -print0 | xargs -0 perl -pi -e \
  's/reward_function\.HunyuanWorldMirror\.src\./models.gen_3d_scene.world_mirror_utils.src./g'
```

Then re-apply modifications 2 and 3 above.
