"""
operators/gen_3d_scene/funcs/

Algorithm steps for scene generation, in the order `Gen3DSceneOperator` runs
them:

| module               | step                                                     |
|----------------------|----------------------------------------------------------|
| `scene_mask.py`      | which pixels carry usable geometry                       |
| `points_to_mesh.py`  | one frame's pointmap → a continuous triangle mesh        |
| `build_scene_mesh.py`| merge every frame into one scene without stacking sheets |

`points_to_mesh` and `build_scene_mesh` together replace upstream's
`create_filter_mask` + `create_image_mesh` + `convert_predictions_to_glb_scene`.
Each module's header explains which specific upstream behaviour it changes and
why.
"""
