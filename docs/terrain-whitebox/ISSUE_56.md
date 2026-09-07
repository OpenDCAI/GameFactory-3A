Six code-generated terrain whiteboxes: plains, hills, basin, canyon, walled town
and city. This compares the original Opus blockouts at `efba5ea` with the GPT-6
revision, rendered from the actual GLBs under identical cameras and lights.

![Opus / GPT-6 comparison](https://raw.githubusercontent.com/DongYu2005/GameFactory-3A/feat/terrain-whitebox-gpt6/docs/terrain-whitebox/comparison.jpg)

[Download GLBs, screenshots, twelve turntable videos and the offline viewer](https://github.com/DongYu2005/GameFactory-3A/releases/download/terrain-whitebox-gpt6-demo/terrain-whitebox-demo.zip)

[Implementation and reproduction commands](https://github.com/DongYu2005/GameFactory-3A/blob/feat/terrain-whitebox-gpt6/docs/terrain-whitebox/README.md)

The revision adds architectural rooflines and foundations, geological benches,
crenellated walls, street-aligned frontage and a taller city skyline. The six
default scenes pass the geometry validator. Screenshots and videos are actual
Three.js GLB renders, not concept images. The code and review remain in the
personal fork pending upstream integration.
