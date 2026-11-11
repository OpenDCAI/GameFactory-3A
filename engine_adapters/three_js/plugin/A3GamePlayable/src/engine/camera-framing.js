/** Geometric framing diagnostics, independent of camera strategy and timing. */
/** Caller updates scene/camera world matrices before inspection. */
export function inspectCameraTargets(
  T,
  scene,
  camera,
  names,
  width = 1280,
  height = 720,
  padding = 0.05,
) {
  return names.map((name) => {
    const object = scene.getObjectByName(name);
    if (!object) return { name, missing: true };
    const worldBounds = new T.Box3();
    const point = new T.Vector3();
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    let vertices = 0;
    let finite = true;
    let supported = true;

    object.traverse((child) => {
      const positions = child.geometry?.attributes?.position;
      if (!positions || (!child.isMesh && !child.isLine && !child.isPoints))
        return;
      for (let parent = child; parent; parent = parent.parent)
        if (!parent.visible) return;
      // Instance transforms and custom shader deformation need a dedicated adapter.
      if (child.isInstancedMesh) {
        supported = false;
        return;
      }
      for (let i = 0; i < positions.count; i++) {
        if (child.getVertexPosition) child.getVertexPosition(i, point);
        else point.fromBufferAttribute(positions, i);
        point.applyMatrix4(child.matrixWorld);
        worldBounds.expandByPoint(point);
        point.project(camera);
        finite &&=
          Number.isFinite(point.x) &&
          Number.isFinite(point.y) &&
          Number.isFinite(point.z);
        min[0] = Math.min(min[0], point.x);
        max[0] = Math.max(max[0], point.x);
        min[1] = Math.min(min[1], point.y);
        max[1] = Math.max(max[1], point.y);
        min[2] = Math.min(min[2], point.z);
        max[2] = Math.max(max[2], point.z);
        vertices++;
      }
    });
    const hasVisibleGeometry = vertices > 0;
    finite &&= hasVisibleGeometry;
    const safe = 1 - 2 * padding;
    return {
      name,
      finite,
      supported,
      hasVisibleGeometry,
      projection_method: 'geometry_vertices',
      worldMin: hasVisibleGeometry ? worldBounds.min.toArray() : null,
      worldMax: hasVisibleGeometry ? worldBounds.max.toArray() : null,
      ndcMin: hasVisibleGeometry ? min : null,
      ndcMax: hasVisibleGeometry ? max : null,
      projectedWidthPx: hasVisibleGeometry
        ? ((max[0] - min[0]) * width) / 2
        : 0,
      projectedHeightPx: hasVisibleGeometry
        ? ((max[1] - min[1]) * height) / 2
        : 0,
      insideFrustum:
        supported &&
        finite &&
        min.every((v) => v >= -1) &&
        max.every((v) => v <= 1),
      insideSafeFrame:
        supported &&
        finite &&
        min[0] >= -safe &&
        min[1] >= -safe &&
        max[0] <= safe &&
        max[1] <= safe &&
        min[2] >= -1 &&
        max[2] <= 1,
    };
  });
}
