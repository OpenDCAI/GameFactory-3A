# Composition Examples

The same three subjects, each built twice: from primitives alone, and with
generated components composed in. Side by side, because the difference between
the two routes is easier to see than to describe.

Every video carries its own measurements — part count, triangles, textures, and
height against what the spec asked for.

## Primitives Only

Every part is a box, cylinder, sphere or lathe profile, joined by `attach`
rather than absolute coordinates. Seconds to build, no GPU and no API key, and
exact where it matters: a rail's slot pitch, a wheelbase, a barrel's diameters.

<table>
  <tr>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/a8e8be8f-3b5b-453c-a3de-fe2dc4dfaad4" width="100%" controls muted playsinline></video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/d6dc4f29-c233-4a3b-b873-055c77070e6b" width="100%" controls muted playsinline></video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/f258e7d7-81d9-4fdc-a1d0-81ae70cbd484" width="100%" controls muted playsinline></video>
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Female knight · 89 parts · 12,420 tris · untextured</sub></td>
    <td align="center"><sub>Assault rifle · 63 parts · 4,040 tris · untextured</sub></td>
    <td align="center"><sub>Race car · 64 parts · 7,224 tris · untextured</sub></td>
  </tr>
</table>

## With Generated Components

The finer of the two. Some parts are meshes from an image-to-3D model, placed by
the same spec: measured off the host, scaled by a single factor so they keep the
proportions they were generated with, and given a stated facing. Surface detail
a primitive cannot reach — at three to eight times the triangles, plus the model
call.

<table>
  <tr>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/ad475ab9-f102-4353-bc99-64f5f532995a" width="100%" controls muted playsinline></video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/e4b525b8-7d54-42ed-b7db-c1dac5d53d62" width="100%" controls muted playsinline></video>
    </td>
    <td width="33%">
      <video src="https://github.com/user-attachments/assets/47237e48-9407-4c76-9d84-05fee356cb2d" width="100%" controls muted playsinline></video>
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Armour and boots fitted to a generated T-pose body · 12 parts · 32,364 tris · 12 textures</sub></td>
    <td align="center"><sub>Generated grip and stock on a stated receiver · 58 parts · 12,927 tris</sub></td>
    <td align="center"><sub>Generated shell on stated running gear · 53 parts · 15,672 tris</sub></td>
  </tr>
</table>

## Character and Garment Motion

Clothing is fitted to an existing character using shoulder, elbow, wrist and
lower-body landmarks, while preserving its materials and UVs. Skinning weights
bind the fitted garment to the character's skeleton. A walk then animates 
the combined character and clothing.

<table>
  <tr>
    <td width="50%" align="center"><sub>Composition 1 · Source assets</sub></td>
    <td width="50%" align="center"><sub>Composition 2 · Source assets</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="https://github.com/user-attachments/assets/f4000dde-0043-41ae-af5f-663c0bb9e14f" width="100%" alt="Source assets for character and garment composition 1" />
    </td>
    <td width="50%" valign="top">
      <img src="https://github.com/user-attachments/assets/a8791e14-df61-42a3-8a9e-42ce1df21085" width="100%" alt="Source assets for character and garment composition 2" />
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <video src="https://github.com/user-attachments/assets/6a9a2f2e-bdee-4d7d-bcf3-2600f04abebc" width="100%" controls muted playsinline></video>
    </td>
    <td width="50%" valign="top">
      <video src="https://github.com/user-attachments/assets/586052f1-f4fb-4f85-a9fe-29503188aa0c" width="100%" controls muted playsinline></video>
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Walking Animation</sub></td>
    <td align="center"><sub>In-place Animation</sub></td>
  </tr>
</table>

## Choosing Between Them

For the rifle and the car, the primitive version is already usable and the
generated shell is a finish pass. Both keep every part a separate glTF node, so
a wheel still turns and a magazine can still be swapped.

The knight is the case where composition is the wrong tool. It took **8
human-agent iterations over about 12 hours** to reach roughly the intended
figure, and the defects were measurement errors rather than modelling ones —
each built cleanly and passed every gate before a render caught it:

- A fetched pauldron was a *left* shoulder. The same mesh on both shoulders put
  the right one's lames inboard over the ribs. The chirality gate checks that
  the two positions mirror, and they did.
- A mesh named `greave_pair.glb` was not a greave but a complete knee-high boot.
  Placed on the `shin` slot it floated with its sole 81 mm off the ground and
  the figure's own foot bare below it.
- A foot-height landmark returned the search window's ceiling rather than the
  foot, so a boot sized to it came out taller than it was wide.

Prefer a spec for rigid assemblies — vehicles, weapons, architecture — where
parts must stay separable and dimensions exact. Generate a figure whole.
