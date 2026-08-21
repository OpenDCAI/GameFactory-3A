class_name A3GameSceneLoader
extends RefCounted


static func instantiate_scene(resource_path: String, parent: Node) -> Dictionary:
	var normalized := resource_path.strip_edges()
	if parent == null:
		return {"ok": false, "error": "parent is required"}
	if not normalized.begins_with("res://") or ".." in normalized.replace("\\", "/").split("/"):
		return {"ok": false, "error": "scene path must be a non-traversing res:// path"}
	if not ResourceLoader.exists(normalized, "PackedScene"):
		return {"ok": false, "error": "PackedScene does not exist: " + normalized}
	var resource := ResourceLoader.load(normalized, "PackedScene")
	if not resource is PackedScene:
		return {"ok": false, "error": "resource is not a PackedScene: " + normalized}
	var instance := (resource as PackedScene).instantiate()
	if instance == null:
		return {"ok": false, "error": "PackedScene could not be instantiated: " + normalized}
	parent.add_child(instance)
	return {"ok": true, "path": normalized, "node": instance}
