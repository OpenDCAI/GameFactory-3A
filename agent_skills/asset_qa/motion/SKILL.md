# Motion Generation

Describe a motion in JSON and let the code solve it into a BVH and an animated GLB. The code lives in `<repo_path>/operators/gen_motion/funcs/vibe_motion_utils`, where `<repo_path>` is the repository root. The parameter reference is the header of `motion_utils/program.py`.

## Workflow

Five steps. If the result is not right, edit the `config` and run again; do not edit the code.

1. **Prepare the skeleton.** Write `skeleton` directly (`name`, `names`, `parents`, world-space `rest`). With no skeleton, pass a mesh plus a `rigging` config and the code fits one; add `skinning` to also skin the mesh.
2. **Write the motion.** `config` has three blocks (they may also be wrapped in `motion`, next to an optional `base` name label; `video` is ignored):
   - `rhythm`: `duration`, `fps`, `events` (named times, may be `{}`); optional `time_unit` (`fraction` by default, or `seconds`), `phases`, `beats`.
   - `style`: `turn_degrees`, `travel_ratio`, `jump_ratio`, `crouch_ratio`, `arm_reach`, `arm_clearance`. Curves can refer to them as `"$turn_degrees"`.
   - `program`: `action`, `forward`, `root`, `parts`, `refine`. `root` is `curves` (x / y / z / yaw, optionally pitch / roll / forward_speed; lengths are in mean leg lengths) or `ballistic`. Each entry in `parts` picks one operator (see Operators). `refine` requires `iterations`, `foot_margin_ratio`, `residual_weight`, `ground_weight`, and optionally `body_ground`, `collision`, `smooth`.
3. **Run.** `GenMotionOperator(output_dir=...).run({"task_type": "vibe", "task_id": ..., "config": ...})`. Also pass `target_mesh_path` to get skinning and the GLB.
4. **Check the report.** Open `vibe_report.json`: `metrics.failures` must be empty. Then read `metrics.ik_max_residual` (can each part reach its target) and `warnings`.
5. **Preview.** Load `motion.bvh` or `animated.glb` in the previewer and go back to step 2 if it looks wrong.

Missing required parameters and unknown fields raise an error; optional parameters fall back to defaults. Example configs are in `<repo_path>/test/vibe_motion_examples/*.json` (`male02_landing.json` is a complete humanoid).

## Operators

Pick one operator per part. `category` is one of `head`, `trunk`, `upper_limb`, `lower_limb`, `tail`. A time can be a number (a fraction of the duration by default), an event name, `phase.start` / `phase.end`, or `"1.25s"`, optionally with an offset such as `"jump.end-0.1s"`.

| Goal | Operator |
|---|---|
| Hold still | `rest` |
| Turn the trunk, head or tail by angle | `rotation` (pitch / twist / roll curves) |
| Point a bone along a direction or at a target | `aim` |
| Swing or raise an arm | `arm_arc` |
| Plant, step or coil a leg or other long limb (3+ joints) | `effector` (`keys` / `plants` / `gait`) |
| Cloaks, hair, tails with inertia | `spring` |
| Carry a prop or place it directly | `translate` |

## Troubleshooting

Look at the report first, then change the config accordingly.

| Symptom | Fix |
|---|---|
| `failures` contains `non_finite_motion` or `bone_length` | The `config` is wrong; check `skeleton` and the curve values. |
| Large `ik_max_residual` | The target is out of reach: reduce the amplitude, widen `flexion` / `limits`, or check that `plants` do not conflict with the root motion. |
| Large `contact_foot_max_speed` | The foot slides while planted; check the `plants` / `gait` time windows. |
| Penetrates the ground | Raise `root.y`, or add `body_ground` to `refine` (needs a mesh). |
| A limb passes through the body | Add `collision` to `refine` (needs a mesh) and raise `iterations`. |
| Joint angles jump | Add `despike_degrees` / `sigma_frames` under `refine.smooth`. |

## Physical Plausibility

Notes on making the motion look physically right.

1. For landings and other contact with the ground, include physical cushioning; a hovering body should bob up and down slightly.
2. For punches and other power moves with no stated timing, make them fast and explosive, and let the body twist along with the striking limb to show force.
3. For flight, adjust the body posture: a dive is parallel to the ground, a hover faces forward and stays perpendicular to the ground.
4. For actions with recoil, such as firing, add the recoil shake.
5. For long limbs that spiral upward, design the motion path first so the flow line is smooth.

If the result is unsatisfactory, the old skill is still available at `<repo_path>/agent_skills/asset_qa/motion/SKILL_old.md`.
