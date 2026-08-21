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
	for _frame in range(90):
		await physics_frame
	var second: Dictionary = game.smoke_snapshot()
	if int(second.get("tick", 0)) <= int(first.get("tick", 0)):
		_fail("physics tick did not advance")
		return
	if first.get("player", []) == second.get("player", []):
		_fail("demo pilot did not move")
		return
	if int(second.get("hazards", 0)) < 3 or int(second.get("pickups", 0)) < 3:
		_fail("gameplay entities were not created")
		return
	print("A3GAME_SMOKE_OK ", JSON.stringify(second))
	quit(0)


func _fail(message: String) -> void:
	push_error("A3GAME_SMOKE_FAIL: " + message)
	quit(1)
