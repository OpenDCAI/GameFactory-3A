# 3AGameFactory-Motion: Agentic Rigging and Reliable Complex Motion Generation for Diverse 3D Assets

<p align="center">
  <a href="https://arxiv.org/abs/2611.30013"><img src="https://img.shields.io/badge/arXiv-2611.30013-b31b1b?style=flat-square&logo=arxiv" alt="arXiv 2611.30013" /></a>
  <a href="https://huggingface.co/datasets/zbhpku/DivDynBench"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Benchmark-DivDynBench-ffd21e?style=flat-square" alt="DivDynBench on Hugging Face" /></a>
</p>

<table>
  <tr>
    <td width="100%"><video src="https://github.com/user-attachments/assets/2dd0289e-b825-4c0e-ba98-a00dcba01ae4" width="100%" controls muted playsinline></video></td>
  </tr>
</table>

## Overview

Animating an arbitrary 3D asset from an instruction needs both structural inference (rigging) and motion control. Existing motion models and benchmarks mostly cover conventional human or animal skeletons with short instructions, while real assets range from multipeds and axial bodies to winged creatures, mixed-limb characters and mechanical structures.

This work has two parts:

- **DivDynBench**, a benchmark for rigging and motion generation on diverse 3D assets. It has 50 assets in six morphology categories (humanoid, multiped, axial, winged, mixed-limb, mechanical) and 850 motion tasks (428 text, 422 numeric). Instructions require multi-phase part coordination and explicit timing, angle, repetition and trajectory constraints.
- **Agentic Motion Framework**, in which an agent writes a compact parametric skeleton configuration and motion program, and a deterministic executor does the rigging, kinematic solving and geometric correction. Writing a small set of semantic parameters instead of frame-by-frame joint coordinates lowers generation cost and improves rigging reliability and motion-control accuracy.

With the same base agents, the framework improves rigging and motion generation over the agents used alone:

| Rigging on raw meshes | Source Fidelity (%) | J2J (lower is better) | mIoU | Joint validity |
|---|---|---|---|---|
| GPT6.1-sol | 67.82 | 0.0749 | 0.2316 | 0.4048 |
| GPT6.1-sol + Ours | **100.00** | **0.0389** | **0.4174** | **0.9718** |
| Claude-ops5.5 | 66.29 | 0.0565 | 0.2610 | 0.5920 |
| Claude-ops5.5 + Ours | **100.00** | 0.0410 | 0.3846 | 0.9209 |

| Motion on reference rigs (out of 100) | Accuracy | Quality | Overall | Cost per case (USD) |
|---|---|---|---|---|
| GPT6.1-sol | 17.27 | 46.53 | 30.19 | 0.024 |
| GPT6.1-sol + Ours | 26.13 | 52.57 | 35.99 | **0.0087** |
| Claude-ops5.5 | 47.03 | 49.06 | 48.19 | 0.065 |
| Claude-ops5.5 + Ours | **50.72** | 52.35 | **50.07** | 0.047 |

See the paper for all baselines, the humanoid-only comparison and the user study.

## What is in this repository

| Part | Location |
|---|---|
| Agent skill: how an agent writes the skeleton and motion config, checks the report and fixes problems | `agent_skills/asset_qa/motion/SKILL.md` |
| Executor: rigging, skinning, motion operators, IK and refinement, BVH / GLB export | `operators/gen_motion/funcs/vibe_motion_utils` |
| Operator entry point | `operators/gen_motion/operator.py` (`GenMotionOperator`) |
| Example configs (humanoid, fox, horse, synthetic chain) | `test/vibe_motion_examples/*.json` |
| Tests | `test/test_vibe_motion.py`, `test/test_vibe_rigging.py` |

The skill is what the agent reads. It describes the config blocks (`rhythm`, `style`, `program`), the motion operators (`rest`, `rotation`, `aim`, `arm_arc`, `effector`, `spring`, `translate`), how to read `vibe_report.json` and `skin_report.json`, and a troubleshooting table. The parameter reference is the header of `motion_utils/program.py`.

## Quick start

1. Use Python 3.11 with the packages the executor imports (NumPy, SciPy and so on; see the imports under `operators/gen_motion/funcs/vibe_motion_utils`).

2. Point an agent at `agent_skills/asset_qa/motion/SKILL.md`, or write a config by hand starting from one of `test/vibe_motion_examples/*.json`.

3. Run the executor. Give it the asset as `target_mesh_path` to get skinning and the animated GLB:

   ```python
   from operators.gen_motion.operator import GenMotionOperator

   result = GenMotionOperator(output_dir="out").run({
       "task_type": "vibe",
       "task_id": "male02",
       "config": config,                      # skeleton or rigging, skinning, motion
       "target_mesh_path": "male02.glb",
   })
   ```

   The result paths include the BVH, the animated GLB, `vibe_report_path` and `skin_report_path`.

4. Open `vibe_report.json`. `metrics.failures` must be empty. Then read `warnings`, render the animated GLB and look at it. If something is wrong, edit the config and run again; the skill's troubleshooting table lists the usual fixes.

Run the tests with:

```bash
python -m unittest test.test_vibe_rigging test.test_vibe_motion
```

## Benchmark and evaluation

DivDynBench is on Hugging Face: <https://huggingface.co/datasets/zbhpku/DivDynBench>. It contains:

- the 50 source meshes (`3d_objects/`, GLB) and the manually designed ground-truth rigs with skinning weights (`rigging_gt/`);
- six `*.jsonl` files with the 850 motion tasks (instruction, rubric, scoring config) and `scoring_rules.json`.

Rigging is evaluated from raw meshes against the ground-truth rigs (Source Fidelity, J2J, mIoU, joint validity, Edge p99, Rigid RMS). Motion is evaluated on the provided reference rigs (Semantic or Accuracy, Completeness, Morphology, Quality, Aesthetics, Overall; scores out of 100). Numeric tasks are checked by program, text tasks by rubric-based visual scoring. The definitions are in the paper and its supplementary material.

## Citation

```bibtex
@article{zeng2026gamefactory3amotion,
  title   = {3AGameFactory-Motion: Agentic Rigging and Reliable Complex Motion Generation for Diverse 3D Assets},
  author  = {Zeng, Bohan and Zhu, Kaixin and Yang, Yifan and Yu, Dongge and Wu, Zihao and Dai, Yifan and Liu, Zhou and Niu, Junbo and Ma, Xiaochen and Ge, Shiran and Meng, Zimo and Liang, Junzhu and Zhang, Huanyao and Liu, Jinhang and Xiao, Tianqi and Zhang, Jinhao and Bai, Tianyi and Zeng, Zhixiong and Zhao, Penghao and Peng, Yuanbo and Zhang, Key and Zhang, Wentao},
  journal = {arXiv preprint arXiv:2611.30013},
  year    = {2026}
}
```

## License

Code: Apache 2.0, see the repository `LICENSE`. For the benchmark assets, see the dataset card on Hugging Face.
