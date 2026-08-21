<p align="center">
  <img src="https://github.com/user-attachments/assets/104f765d-44aa-4880-8748-f6c02381e11d" alt="3AGameFactory" width="256" />
</p>

<p align="center">
  <a href="https://github.com/OpenDCAI/GameFactory-3A/stargazers"><img src="https://img.shields.io/github/stars/OpenDCAI/GameFactory-3A?style=flat-square&logo=github&color=ffca28" alt="GitHub stars" /></a>
  <a href="https://github.com/OpenDCAI/GameFactory-3A/network/members"><img src="https://img.shields.io/github/forks/OpenDCAI/GameFactory-3A?style=flat-square&logo=github&color=90a4ae" alt="GitHub forks" /></a>
  <a href="https://github.com/OpenDCAI/GameFactory-3A/issues"><img src="https://img.shields.io/github/issues/OpenDCAI/GameFactory-3A?style=flat-square&logo=github" alt="GitHub issues" /></a>
  <a href="https://github.com/OpenDCAI/GameFactory-3A/pulls"><img src="https://img.shields.io/github/issues-pr/OpenDCAI/GameFactory-3A?style=flat-square&logo=github" alt="Pull requests" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square" alt="License: Apache 2.0" /></a>
  <img src="https://img.shields.io/badge/Engines-UE5%20%7C%20Blender%20%7C%20Unity%20%7C%20Godot%20%7C%20three.js-6a4cff?style=flat-square" alt="支持的引擎" />
  <img src="https://img.shields.io/badge/Assets-Image%20%7C%203D%20%7C%20Motion%20%7C%20Audio%20%7C%20CG%20Video-00897b?style=flat-square" alt="资产能力覆盖" />
</p>

# 3AGameFactory

**3AGameFactory 让 Coding Agent 根据游戏需求生成可用于游戏构建的资产与引擎代码。**

> **3AGameFactory 是一个全面的开源 3A 游戏生成 Skill 与资产框架。** 它覆盖图片、3D 资产、动作、音频与 CG 视频生成，并支持使用 **UE5、Blender、Unity、Godot 4 和 three.js** 构建游戏。

<p align="center">
  <b><a href="#quick-start">快速开始</a></b>
  ·
  <a href="README.md">English</a>
</p>

---

## 游戏演示

以下是 3AGameFactory 生成的不同美术风格、不同视角游戏的实机操作录屏。

### Unity

对战、FPS 与赛车游戏：对战人物全部由 Meshy 生成，并使用我们自己的 Puppeteer + MoMask 完成绑骨与动作；FPS 的枪械与赛车游戏的车辆由 Hunyuan3D 生成，其人物与动作来自 Mixamo，场景则取自 Unity 资产库。

<table>
  <tr>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/5a090980-1b88-4913-a92d-1e7fb6745a91" width="100%" controls muted playsinline>
      </video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/79e33a78-79e0-4373-bec9-853d55a4a38c" width="100%" controls muted playsinline>
      </video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/4c6b7937-2e3d-4609-b4d2-5f9109a1f9a8" width="100%" controls muted playsinline>
      </video>
    </td>
  </tr>
</table>

### UE5

对战、RPG 与 FPS 游戏：对战中我方操控人物，以及 RPG 中的人物、石碑与宝箱由 Meshy 生成，FPS 的枪械由 Hunyuan3D 生成；其余人物与全部动作来自 Mixamo 等开源资产，场景均为开源资产。

<table>
  <tr>
    <td width="33%"><video src="https://github.com/user-attachments/assets/c7c0fbfe-cfbc-4a10-b279-c666ae1364da" width="100%" controls muted playsinline></video></td>
    <td width="33%"><video src="https://github.com/user-attachments/assets/810ced06-7c0e-4e74-a0f5-588c9cad8d1e" width="100%" controls muted playsinline></video></td>
    <td width="33%"><video src="https://github.com/user-attachments/assets/8f90cc59-548a-4cd9-a3a8-7d8c4e2e412b" width="100%" controls muted playsinline></video></td>
  </tr>
</table>

### Blender

对战与赛车游戏均使用下载的简单资产；探索游戏与 FPS 游戏则由下载的场景贴图与 Meshy 生成的物体实现。

<table>
  <tr>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/0c686db2-2ae4-4d3e-9aeb-311bf8616267" width="100%" controls muted playsinline></video>
    </td>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/f2341548-e484-40fa-9b03-a63ff9f4eee8" width="100%" controls muted playsinline></video>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/0ae06144-28f1-459c-b418-10ef1dc59ae4" width="100%" controls muted playsinline></video>
    </td>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/713db0bd-0393-445b-b3a1-39863e7df49a" width="100%" controls muted playsinline></video>
    </td>
  </tr>
</table>

### three.js

下方演示依次展示对战游戏、RPG 探索游戏、第一人称射击（FPS）游戏和赛车游戏。

<table>
  <tr>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/904ccddd-5a51-446e-aa38-bcb4ee8f4e9a" width="100%" controls muted playsinline></video>
    </td>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/6dc42e07-3b58-43e5-8005-cd83fbce1db4" width="100%" controls muted playsinline></video>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/3b679c56-7fcd-42aa-985b-3c3cd4e05159" width="100%" controls muted playsinline></video>
    </td>
    <td width="50%">
      <video src="https://github.com/user-attachments/assets/12786f57-154c-4b74-b1a0-d68efd21ee50" width="100%" controls muted playsinline></video>
    </td>
  </tr>
</table>

## CG 视频演示

下方演示包括 F1 赛车游戏开场、奇幻探索 RPG 中段剧情、反恐战术 FPS 宣传片，
以及双人对战游戏的大招演出。

> 演示使用 [MiniMax H3](https://huggingface.co/Comfy-Org/MiniMax-H3)
> 以 720P 本地生成。如有需要，可选用更高本地分辨率或 Seedance 等云端 API
> 模型，可能获得更优效果，但成本也更高。

<table>
  <tr>
    <td width="50%"><video src="https://github.com/user-attachments/assets/02bfe9fe-f5bf-46f2-81ab-371c57120b42" width="100%" controls muted playsinline></video></td>
    <td width="50%"><video src="https://github.com/user-attachments/assets/4408ca34-016b-4291-a282-2219abd42192" width="100%" controls muted playsinline></video></td>
  </tr>
  <tr>
    <td width="50%"><video src="https://github.com/user-attachments/assets/3ea93ea3-050a-44a4-a326-868943d2950e" width="100%" controls muted playsinline></video></td>
    <td width="50%"><video src="https://github.com/user-attachments/assets/1e6d59a1-d0f2-4bf5-bfb6-70679e253760" width="100%" controls muted playsinline></video></td>
  </tr>
</table>

---

<a id="quick-start"></a>

## 快速开始：让 Coding Agent 生成游戏

3AGameFactory 由 Coding Agent 驱动，它读取本项目的 Skills 并调用相应 Pipeline，
例如 [Codex](https://github.com/openai/codex)、
[Claude Code](https://github.com/anthropics/claude-code) 或
[Gemini CLI](https://github.com/google-gemini/gemini-cli)。

```text
1. 打开 Coding Agent，例如 Codex、Claude Code（CC）或其他兼容 Agent。
2. cd GameFactory-3A
3. 告诉 Agent 你的游戏需求，并要求它先阅读 agent_skills/setting_overview.md。
```

<p><strong><span style="color: #d1242f;">重要提示</span></strong><br>
`agent_skills/setting_overview.md` 是使用 3AGameFactory 生成资产、玩法、UI
和特定引擎游戏时的入口文档。它会将 Agent 路由到对应的资产 Skill 和引擎
API 上下文。

### 为 3AGameFactory 框架贡献代码

这与“使用框架生成游戏”是两条独立路径。若要新增或修改模型封装、Operator
或 Pipeline Runner，请从
[`agent_skills/develop_harness/README.md`](agent_skills/develop_harness/README.md)
开始，并先运行其中定义的 CPU smoke harness，再使用模型权重或 GPU。

---

<a id="capabilities"></a>

## 3AGameFactory 能做什么

| 能力 | 产物 | 主要 Pipeline 位置 |
|---|---|---|
| 图片与 T-pose 预处理 | 源图像、角色可用输入 | `pipeline/assets_gen/gen_tpose_image/` |
| 3D 物体生成 | 道具、角色、武器与可复用网格 | `pipeline/assets_gen/gen_3d_object/` |
| 3D 场景生成 | 重建的室内场景或组合式环境 | `pipeline/assets_gen/gen_3d_scene/` |
| 动作生成 | 骨骼、生成动作、重定向动画片段 | `pipeline/assets_gen/gen_motion/` |
| 音频生成 | 对话、音效、环境声与 WAV 资产 | `pipeline/assets_gen/gen_audio/` |
| CG 视频生成 | 文本、首帧、首尾帧、参考图驱动的 MP4 | `pipeline/assets_gen/gen_cg_video/` |
| 玩法生成 | 引擎原生的机制与运行时行为 | `pipeline/code_gen/gen_mechanic/` |
| UI 生成 | HUD、菜单、界面与交互流程 | `pipeline/code_gen/gen_ui/` |
| 完整游戏切片 | 资产、玩法、UI 与评测的协同结果 | 由 Agent 依据 `agent_skills/setting_overview.md` 编排 |

<a id="engines"></a>

### 支持的游戏构建引擎

| 引擎 | Agent 上下文 | 参考实现 |
|---|---|---|
| UE5 | `agent_skills/engine_context/ue5_api.md` | `engine_adapters/ue5/` |
| Blender | `agent_skills/engine_context/blender_api.md` | `engine_adapters/blender/` |
| Unity | `agent_skills/engine_context/unity3d_api.md` | `engine_adapters/unity3d/` |
| Godot 4 | `agent_skills/engine_context/godot_api.md` | `engine_adapters/godot/` |
| three.js | `agent_skills/engine_context/three_js_api.md` | `engine_adapters/three_js/` |

---

<a id="layout"></a>

## 项目目录

```text
GameFactory-3A/
├── agent_skills/               # 供 Agent 阅读的工作流、QA Skill 与引擎 API 上下文
│   ├── setting_overview.md     # 游戏生成 Agent 从这里开始
│   ├── asset_qa/               # 资产生成与视觉 QA Skill
│   ├── code_gen/               # 将已验收资产整合为玩法和 UI 的 Skill
│   ├── develop_harness/        # models → operators → pipeline 的贡献者契约
│   └── engine_context/         # UE5、Blender、Unity、Godot、three.js 与浏览器 API 上下文
├── engine_adapters/            # 引擎参考代码与公开 Adapter API
├── models/                     # 本地模型与云模型封装
├── operators/                  # 组合已加载模型的任务逻辑
├── pipeline/                   # 生成与评测入口
│   ├── assets_gen/             # 图片、3D、场景、动作、音频与 CG 视频任务
│   ├── code_gen/               # 玩法（gen_mechanic）与 UI（gen_ui）代码生成
│   └── common/                 # 共享辅助模块；paths.py 是所有输入输出路径的唯一来源
├── scripts/                    # 环境配置、引擎启动器与导入工具
│   ├── asset_env_setup/        # 按资产任务组织的环境配置，含 gen_motion 运行时与权重安装
│   └── engine_install/         # UE5、Blender、Unity、Godot、three.js 的安装与启动脚本
├── test/                       # 用于验证流程实际可运行的契约、集成与 smoke 脚本
├── test_data/                  # 示例需求；生成的游戏结果位于 outputs/
└── third_party/                # 检出的外部仓库，例如 trimesh 与引擎材质/资产库
```

生成产物位于 `test_data/outputs/`，并按照游戏、运行、任务类别和任务 ID
组织。Agent 与贡献者应使用 `pipeline/common/paths.py`，不要手工拼接输出路径。

## 加入我们的社区

欢迎加入我们的微信群，与我们交流项目使用、开发相关问题以及反馈建议。

<p align="center">
  <img width="396" height="396" alt="微信群二维码" src="https://github.com/user-attachments/assets/dffb9c54-cd72-449a-872d-27ecca0fa1c9" />
</p>

<p align="center">
  扫描二维码加入微信群
</p>

> 如果二维码失效，请在 GitHub Issues 中留言，我们会及时更新。

---

<a id="citation"></a>

## 引用

```bibtex
@misc{gamefactory3a,
  title        = {3AGameFactory: Open-Source 3A Game Generation Skills and Asset Framework},
  author       = {OpenDCAI},
  year         = {2026},
  howpublished = {\url{https://github.com/OpenDCAI/GameFactory-3A}},
  note         = {Open-source software repository}
}
```

---

## 许可证

3AGameFactory 基于 [Apache License 2.0](LICENSE) 开源。

第三方引擎、模型、权重以及从外部素材库获取的资产均遵循各自的许可证。
在将生成内容用于正式产品前，请先确认对应提供方的授权条款。
