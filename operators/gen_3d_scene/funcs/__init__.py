"""
operators/gen_3d_scene/funcs/

Algorithm steps for scene generation.

Two routes reach a scene, and they start from opposite ends.

Reconstruction — a scene recovered from images, in the order
`Gen3DSceneOperator` runs them:

| module               | step                                                     |
|----------------------|----------------------------------------------------------|
| `scene_mask.py`      | which pixels carry usable geometry                       |
| `points_to_mesh.py`  | one frame's pointmap → a continuous triangle mesh        |
| `build_scene_mesh.py`| merge every frame into one scene without stacking sheets |

`points_to_mesh` and `build_scene_mesh` together replace upstream's
`create_filter_mask` + `create_image_mesh` + `convert_predictions_to_glb_scene`.
Each module's header explains which specific upstream behaviour it changes and
why.

Construction — a scene written as code, with no images at all:

| module                     | step                                               |
|----------------------------|----------------------------------------------------|
| `terrain_code_edit.py`     | terrain, layout, sizing and materials as values     |
| `terrain_code_template/`   | `landforms.py` the ground, `foreground.py` what is on it |

The greybox is built and checked first, then individual props are swapped for
generated meshes and textures are staged. `scene_assets.py` and
`appearance_assets.py` supply the files both routes finish with.
"""
