extends SceneTree


func _initialize() -> void:
	var report_path := OS.get_environment("A3GAME_GODOT_BINDING_REPORT")
	var report := {
		"schema_version": "gamefactory3a.godot.material_binding_report.v1",
		"ok": false,
		"bindings": [],
		"errors": [],
	}
	var job_path := OS.get_environment("A3GAME_GODOT_BINDING_JOB")
	if job_path.is_empty() or report_path.is_empty():
		report["errors"].append("A3GAME_GODOT_BINDING_JOB and A3GAME_GODOT_BINDING_REPORT are required")
		_finish(report_path, report, 2)
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(job_path))
	if not (parsed is Dictionary):
		report["errors"].append("Material binding job must be a JSON object")
		_finish(report_path, report, 2)
		return
	var material_resource := str(parsed.get("material", ""))
	var material = load(material_resource)
	if not (material is Material):
		report["errors"].append("Material could not be loaded: %s" % material_resource)
		_finish(report_path, report, 3)
		return
	var bindings = parsed.get("bindings", [])
	if not (bindings is Array) or bindings.is_empty():
		report["errors"].append("Material binding job has no scene bindings")
		_finish(report_path, report, 2)
		return
	for binding in bindings:
		if not (binding is Dictionary):
			report["errors"].append("Material binding entries must be objects")
			break
		var source_resource := str(binding.get("source_resource", ""))
		var target_resource := str(binding.get("target_resource", ""))
		var packed_source = load(source_resource)
		if not (packed_source is PackedScene):
			report["errors"].append("Mesh scene could not be loaded: %s" % source_resource)
			break
		var instance = packed_source.instantiate()
		if not (instance is Node):
			report["errors"].append("Mesh scene could not be instantiated: %s" % source_resource)
			break
		var changed := _apply_material(instance, material)
		if changed == 0:
			instance.free()
			report["errors"].append("Mesh scene has no MeshInstance3D nodes: %s" % source_resource)
			break
		var packed_target := PackedScene.new()
		var pack_error := packed_target.pack(instance)
		instance.free()
		if pack_error != OK:
			report["errors"].append("Could not pack bound scene %s: %s" % [target_resource, error_string(pack_error)])
			break
		var target_directory := ProjectSettings.globalize_path(target_resource.get_base_dir())
		var directory_error := DirAccess.make_dir_recursive_absolute(target_directory)
		if directory_error != OK and directory_error != ERR_ALREADY_EXISTS:
			report["errors"].append("Could not create bound scene directory: %s" % error_string(directory_error))
			break
		var save_error := ResourceSaver.save(packed_target, target_resource)
		if save_error != OK:
			report["errors"].append("Could not save bound scene %s: %s" % [target_resource, error_string(save_error)])
			break
		report["bindings"].append({
			"artifact_id": str(binding.get("artifact_id", "")),
			"source_resource": source_resource,
			"target_resource": target_resource,
			"material": material_resource,
			"mesh_instance_count": changed,
		})
	if report["errors"].is_empty() and report["bindings"].size() == bindings.size():
		report["ok"] = true
		_finish(report_path, report, 0)
	else:
		_finish(report_path, report, 4)


func _apply_material(node: Node, material: Material) -> int:
	var changed := 0
	if node is MeshInstance3D:
		node.material_override = material
		changed += 1
	for child in node.get_children():
		changed += _apply_material(child, material)
	return changed


func _finish(report_path: String, report: Dictionary, exit_code: int) -> void:
	if not report_path.is_empty():
		var handle := FileAccess.open(report_path, FileAccess.WRITE)
		if handle != null:
			handle.store_string(JSON.stringify(report, "  ") + "\n")
			handle.close()
	quit(exit_code)
