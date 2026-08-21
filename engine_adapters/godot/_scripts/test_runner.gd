extends SceneTree


func _init() -> void:
	call_deferred("_run_tests")


func _run_tests() -> void:
	var root := OS.get_environment("A3GAME_GODOT_TEST_ROOT")
	if root.is_empty():
		root = "res://tests"
	var name_filter := OS.get_environment("A3GAME_GODOT_TEST_FILTER")
	var report_path := OS.get_environment("A3GAME_GODOT_TEST_REPORT")
	var tests: Array[Dictionary] = []
	var directory := DirAccess.open(root)
	if directory == null:
		tests.append({
			"name": "test_discovery",
			"file": root,
			"status": "failed",
			"message": "Test directory was not found",
		})
	else:
		for filename in directory.get_files():
			if not filename.begins_with("test_") or not filename.ends_with(".gd"):
				continue
			if not name_filter.is_empty() and filename.findn(name_filter) < 0:
				continue
			var path := root.path_join(filename)
			var started := Time.get_ticks_msec()
			var test_script := load(path)
			if test_script == null:
				tests.append(_case(filename, path, false, "Script could not be loaded", started))
				continue
			var instance = test_script.new()
			if not instance.has_method("run_test"):
				tests.append(_case(filename, path, false, "run_test() is required", started))
				continue
			var value = instance.run_test()
			if typeof(value) == TYPE_BOOL:
				tests.append(_case(filename, path, value, "", started))
			elif value is Dictionary:
				if not value.has("ok"):
					tests.append(_case(
						filename,
						path,
						false,
						"run_test() Dictionary result must contain a boolean 'ok' field",
						started,
					))
					continue
				if typeof(value["ok"]) != TYPE_BOOL:
					tests.append(_case(
						filename,
						path,
						false,
						"run_test() Dictionary field 'ok' must be a boolean",
						started,
					))
					continue
				var passed: bool = value["ok"]
				tests.append(_case(
					str(value.get("name", filename)),
					path,
					passed,
					str(value.get("message", "")),
					started,
				))
			else:
				tests.append(_case(
					filename,
					path,
					false,
					"run_test() must return bool or Dictionary, got %s" % type_string(typeof(value)),
					started,
				))
	var failed := 0
	for test in tests:
		if test.get("status") == "failed":
			failed += 1
	var report := {
		"schema_version": "gamefactory3a.godot.tests.v1",
		"tests": tests,
		"total": tests.size(),
		"failed": failed,
		"passed": tests.size() - failed,
	}
	if not report_path.is_empty():
		var report_file := FileAccess.open(report_path, FileAccess.WRITE)
		if report_file != null:
			report_file.store_string(JSON.stringify(report, "  "))
	print(JSON.stringify(report))
	quit(1 if failed > 0 or tests.is_empty() else 0)


func _case(name: String, path: String, passed: bool, message: String, started: int) -> Dictionary:
	return {
		"name": name,
		"file": path,
		"status": "passed" if passed else "failed",
		"message": message,
		"duration_ms": Time.get_ticks_msec() - started,
	}
