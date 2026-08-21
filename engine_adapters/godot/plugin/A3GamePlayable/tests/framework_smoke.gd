extends SceneTree


const ROOT := "res://addons/a3game_playable/"
var failures: PackedStringArray = []


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var input_script = load(ROOT + "input_state.gd")
	var normalized: Dictionary = input_script.normalize({
		"move_x": 2.0, "move_y": -2.0, "run": true, "jump": false,
		"yaw": 0.4, "pitch": 9.0, "seq": 4, "timestamp": 1.25,
	})
	_check(normalized.get("ok") == true, "normalized input failed")
	_check(normalized["input"]["move_x"] == 1.0, "move_x was not clamped")
	_check(normalized["input"]["pitch"] <= PI * 0.5, "pitch was not clamped")
	_check(input_script.normalize({"move_x": NAN}).get("ok") == false, "non-finite input was accepted")

	var identity = load(ROOT + "identity.gd").new()
	var identity_result: Dictionary = identity.configure({
		"world_id": "world_test", "participant_id": "p1", "entity_id": "e1",
	})
	_check(identity_result.get("ok") == true, "identity configure failed")
	_check(identity.snapshot().get("entity_id") == "e1", "identity snapshot drifted")
	identity.free()

	var parent := Node3D.new()
	root.add_child(parent)
	var runtime = load(ROOT + "runtime.gd").new()
	_check(runtime is Node, "runtime autoload script did not instantiate")
	_check(runtime.sessions_snapshot().is_empty(), "runtime initial state was not empty")
	runtime.free()
	var runtime_entity = load(ROOT + "runtime_entity.gd").new()
	parent.add_child(runtime_entity)
	await process_frame
	_check(runtime_entity.is_in_group("a3game_runtime_entity"), "runtime entity did not register")
	_check(runtime_entity.configure_a3game_identity({
		"world_id": "world_test", "participant_id": "p1", "entity_id": "runtime_e1",
	}).get("ok") == true, "runtime entity identity failed")
	runtime_entity.apply_a3game_input({"move_x": 2.0, "seq": 2})
	_check(runtime_entity.last_input.get("move_x") == 1.0, "runtime entity input was not normalized")
	var scene_loader = load(ROOT + "scene_loader.gd")
	var scene_result: Dictionary = scene_loader.instantiate_scene(
		ROOT + "tests/fixture.tscn", parent
	)
	_check(scene_result.get("ok") == true, "scene loader rejected a PackedScene")
	_check(
		scene_result.get("node") != null and scene_result["node"].get_parent() == parent,
		"scene loader did not attach the instance",
	)
	_check(scene_loader.instantiate_scene("../escape.tscn", parent).get("ok") == false, "scene loader accepted traversal")

	var animation_director = load(ROOT + "animation_director.gd")
	_check(animation_director.play(parent, &"missing").get("ok") == false, "missing animation did not fail")
	var collision_probe = load(ROOT + "collision_probe.gd")
	_check(collision_probe.raycast(null, Vector3.ZERO, Vector3.ONE).get("ok") == false, "null World3D did not fail")

	var visual_kit = load(ROOT + "visual_kit.gd")
	var material: StandardMaterial3D = visual_kit.pbr_material(Color.RED, 0.25, 0.8, Color("220000"))
	_check(is_equal_approx(material.metallic, 0.8) and material.emission_enabled, "PBR material contract failed")
	var sun = visual_kit.create_sun()
	_check(sun is DirectionalLight3D, "sun helper returned wrong type")
	sun.free()

	var hud = load(ROOT + "hud_layer.gd").new()
	root.add_child(hud)
	await process_frame
	hud.set_title("Framework smoke")
	hud.set_status({"score": 10, "state": "ready"})
	_check(hud.get_child_count() == 2, "HUD layer did not build labels")
	parent.queue_free()
	hud.queue_free()
	await process_frame
	await process_frame

	if not failures.is_empty():
		push_error("A3GAME_FRAMEWORK_SMOKE_FAIL: " + "; ".join(failures))
		quit(1)
		return
	print("A3GAME_FRAMEWORK_SMOKE_OK capabilities=9")
	quit(0)


func _check(condition: bool, message: String) -> void:
	if not condition:
		failures.append(message)
