class_name A3GameCollisionProbe
extends RefCounted


static func raycast(
	world: World3D,
	from: Vector3,
	to: Vector3,
	collision_mask: int = 0xFFFFFFFF,
	exclude: Array[RID] = [],
) -> Dictionary:
	if world == null:
		return {"ok": false, "error": "World3D is required"}
	if from.is_equal_approx(to):
		return {"ok": false, "error": "ray endpoints must differ"}
	var query := PhysicsRayQueryParameters3D.create(from, to, collision_mask, exclude)
	query.collide_with_areas = true
	query.collide_with_bodies = true
	var hit := world.direct_space_state.intersect_ray(query)
	if hit.is_empty():
		return {"ok": true, "hit": false, "distance": from.distance_to(to)}
	return {
		"ok": true,
		"hit": true,
		"position": hit.get("position", Vector3.ZERO),
		"normal": hit.get("normal", Vector3.UP),
		"collider": hit.get("collider"),
		"collider_id": hit.get("collider_id", 0),
		"shape": hit.get("shape", -1),
		"distance": from.distance_to(hit.get("position", from)),
	}


static func overlap_sphere(
	world: World3D,
	center: Vector3,
	radius: float,
	collision_mask: int = 0xFFFFFFFF,
	max_results: int = 32,
) -> Dictionary:
	if world == null:
		return {"ok": false, "error": "World3D is required"}
	if not is_finite(radius) or radius <= 0.0:
		return {"ok": false, "error": "radius must be finite and positive"}
	if max_results < 1 or max_results > 256:
		return {"ok": false, "error": "max_results must be between 1 and 256"}
	var shape := SphereShape3D.new()
	shape.radius = radius
	var query := PhysicsShapeQueryParameters3D.new()
	query.shape = shape
	query.transform = Transform3D(Basis.IDENTITY, center)
	query.collision_mask = collision_mask
	query.collide_with_areas = true
	query.collide_with_bodies = true
	var hits := world.direct_space_state.intersect_shape(query, max_results)
	return {"ok": true, "count": hits.size(), "hits": hits}
