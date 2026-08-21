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
	for _frame in range(75):
		await physics_frame
	var first: Dictionary = game.smoke_snapshot()
	for _frame in range(120):
		await physics_frame
	var second: Dictionary = game.smoke_snapshot()
	if int(second.get("tick", 0)) <= int(first.get("tick", 0)):
		_fail("physics tick did not advance")
		return
	var first_position := Vector2(first["ball"][0], first["ball"][1])
	var second_position := Vector2(second["ball"][0], second["ball"][1])
	if first_position.distance_to(second_position) < 20.0:
		_fail("RigidBody2D ball did not travel")
		return
	if int(second.get("bumpers", 0)) != 5 or int(second.get("flippers", 0)) != 2:
		_fail("physical table was not constructed")
		return
	var velocity := Vector2(second["velocity"][0], second["velocity"][1])
	if velocity.length() < 100.0:
		_fail("ball physics velocity is implausibly low: " + str(velocity))
		return
	print("A3GAME_SMOKE_OK ", JSON.stringify(second))
	quit(0)


func _fail(message: String) -> void:
	push_error("A3GAME_SMOKE_FAIL: " + message)
	quit(1)
