# Validation

Validated on Windows with Python 3.12 in the existing `aibasis` environment.
Playwright was installed into a project-local temporary dependency directory;
scene generation and unit tests have no new third-party dependencies.

- `python test/test_3d_scene_code.py`: **212 tests passed, zero skipped**.
- All six revised default scenes: zero `check_scene` problems.
- Both versions exported: 12 GLBs and 12 PNG renders.
- 12 turntables checked with ffprobe: 1280 x 960, 24 fps, 120 frames, 5 seconds.
- Three.js rendering: nonblank canvas and changed pixels after camera motion
  verified for every scene/version; no page JavaScript errors.
- Viewer: scene switching, material modes, reset, automatic rotation,
  synchronized mouse orbit, and both mobile canvases exercised with Playwright.
- Desktop 1280 x 960 and mobile 390 x 844 screenshots inspected.
- `git diff --check`: passed.

| Revised Scene | Props | GLB Bytes | Geometry Problems |
| --- | ---: | ---: | ---: |
| Plains | 198 | 1,195,872 | 0 |
| Hills | 168 | 1,222,344 | 0 |
| Basin | 148 | 1,115,216 | 0 |
| Canyon | 91 | 935,996 | 0 |
| Walled town | 312 | 1,395,768 | 0 |
| City | 876 | 2,288,824 | 0 |

The baseline is `efba5ea`. Reproduction details are in [README.md](README.md).
Source hashes and render measurements accompany the downloadable demo bundle.
No Unity, Unreal or Godot playtest was performed for this procedural GLB change.
