extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var packed := load("res://main.tscn") as PackedScene
	if packed == null:
		_fail("main.tscn did not load as PackedScene")
		return
	var game := packed.instantiate()
	root.add_child(game)
	for _frame in range(90):
		await physics_frame
	var first: Dictionary = game.smoke_snapshot()
	for _frame in range(150):
		await physics_frame
	var second: Dictionary = game.smoke_snapshot()
	if int(second.get("tick", 0)) <= int(first.get("tick", 0)):
		_fail("physics tick did not advance")
		return
	var first_position := Vector3(first["vehicle"][0], first["vehicle"][1], first["vehicle"][2])
	var second_position := Vector3(second["vehicle"][0], second["vehicle"][1], second["vehicle"][2])
	if first_position.distance_to(second_position) < 1.0:
		_fail("rally vehicle did not travel")
		return
	if int(second.get("checkpoint_count", 0)) != 8:
		_fail("checkpoint course was not constructed")
		return
	if float(second.get("speed", 0.0)) < 5.0:
		_fail("vehicle did not accelerate")
		return
	print("A3GAME_SMOKE_OK ", JSON.stringify(second))
	quit(0)


func _fail(message: String) -> void:
	push_error("A3GAME_SMOKE_FAIL: " + message)
	quit(1)
