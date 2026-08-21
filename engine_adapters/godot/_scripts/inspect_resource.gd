extends SceneTree


func _init() -> void:
	call_deferred("_inspect")


func _inspect() -> void:
	var resource_path := OS.get_environment("A3GAME_GODOT_RESOURCE")
	var report_path := OS.get_environment("A3GAME_GODOT_INSPECT_REPORT")
	var report := {
		"schema_version": "gamefactory3a.godot.resource_inspection.v1",
		"ok": false,
		"resource_path": resource_path,
		"resource_class": "",
		"is_packed_scene": false,
		"is_animation_library": false,
		"is_animation": false,
		"is_texture_2d": false,
		"is_audio_stream": false,
		"is_material": false,
		"instantiable": false,
		"nodes": [],
		"animations": [],
		"animation_details": [],
		"skeletons": [],
		"skinned_meshes": [],
		"error": "",
	}
	var resource = load(resource_path)
	if resource == null:
		report["error"] = "Resource could not be loaded"
	else:
		report["resource_class"] = resource.get_class()
		report["is_packed_scene"] = resource is PackedScene
		report["is_animation_library"] = resource is AnimationLibrary
		report["is_animation"] = resource is Animation
		report["is_texture_2d"] = resource is Texture2D
		report["is_audio_stream"] = resource is AudioStream
		report["is_material"] = resource is Material
		if resource is PackedScene:
			var instance: Node = resource.instantiate()
			if instance == null:
				report["error"] = "PackedScene could not be instantiated"
			else:
				report["ok"] = true
				report["instantiable"] = true
				_collect_scene(instance, report, ".", instance)
				instance.free()
		elif resource is AnimationLibrary:
			report["ok"] = true
			_collect_animation_library(resource, report, "", ".", null, null)
		elif resource is Animation:
			report["ok"] = true
			_collect_animation(resource, report, "", resource_path.get_file(), ".", null, null)
		else:
			report["ok"] = true
	_write_report(report_path, report)
	quit(0 if bool(report["ok"]) else 1)


func _collect_scene(node: Node, report: Dictionary, relative_path: String, scene_root: Node) -> void:
	report["nodes"].append({"name": node.name, "class": node.get_class(), "path": relative_path})
	if node is Skeleton3D:
		var bones: Array[String] = []
		for bone_index in node.get_bone_count():
			bones.append(str(node.get_bone_name(bone_index)))
		report["skeletons"].append({
			"path": relative_path,
			"bone_count": node.get_bone_count(),
			"bones": bones,
		})
	if node is MeshInstance3D:
		var skin = node.get("skin")
		var skeleton_path = str(node.get("skeleton"))
		var resolved_skeleton_path := ""
		var skeleton_resolved := false
		if not skeleton_path.is_empty():
			var skeleton_target: Node = node.get_node_or_null(NodePath(skeleton_path))
			if skeleton_target is Skeleton3D:
				resolved_skeleton_path = str(scene_root.get_path_to(skeleton_target))
				skeleton_resolved = skeleton_target.get_bone_count() > 0
		if skin != null or not skeleton_path.is_empty():
			report["skinned_meshes"].append({
				"path": relative_path,
				"skeleton": skeleton_path,
				"skeleton_path": resolved_skeleton_path,
				"skeleton_resolved": skeleton_resolved,
				"has_skin": skin != null,
			})
	if node is AnimationPlayer:
		for library_name in node.get_animation_library_list():
			var library: AnimationLibrary = node.get_animation_library(library_name)
			_collect_animation_library(library, report, str(library_name), relative_path, node, scene_root)
	for child in node.get_children():
		var child_path := str(child.name) if relative_path == "." else relative_path.path_join(str(child.name))
		_collect_scene(child, report, child_path, scene_root)


func _collect_animation_library(library: AnimationLibrary, report: Dictionary, library_name: String, player_path: String, player: AnimationPlayer, scene_root: Node) -> void:
	for animation_name in library.get_animation_list():
		var animation: Animation = library.get_animation(animation_name)
		_collect_animation(animation, report, library_name, str(animation_name), player_path, player, scene_root)


func _collect_animation(animation: Animation, report: Dictionary, library_name: String, animation_name: String, player_path: String, player: AnimationPlayer, scene_root: Node) -> void:
	var tracks: Array[Dictionary] = []
	for track_index in animation.get_track_count():
		var track_path: NodePath = animation.track_get_path(track_index)
		var path := str(track_path)
		var node_path := str(track_path.get_concatenated_names())
		var bone := str(track_path.get_subname(0)) if track_path.get_subname_count() > 0 else ""
		var target_class := ""
		var targets_skeleton_bone := false
		if player != null and scene_root != null:
			var animation_root: Node = player.get_node_or_null(player.get_root_node())
			if animation_root != null:
				var target: Node = animation_root.get_node_or_null(NodePath(node_path))
				if target != null:
					node_path = str(scene_root.get_path_to(target))
					target_class = target.get_class()
					targets_skeleton_bone = target is Skeleton3D and not bone.is_empty() and target.find_bone(bone) >= 0
		tracks.append({
			"path": path,
			"node_path": node_path,
			"bone": bone,
			"type": int(animation.track_get_type(track_index)),
			"target_class": target_class,
			"targets_skeleton_bone": targets_skeleton_bone,
		})
	report["animations"].append(animation_name)
	report["animation_details"].append({
		"player_path": player_path,
		"library": library_name,
		"name": animation_name,
		"length": animation.length,
		"track_count": animation.get_track_count(),
		"tracks": tracks,
	})


func _write_report(path: String, report: Dictionary) -> void:
	if path.is_empty():
		return
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(report, "  "))
