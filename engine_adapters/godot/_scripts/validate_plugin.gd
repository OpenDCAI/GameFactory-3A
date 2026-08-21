extends SceneTree


func _init() -> void:
	call_deferred("_validate")


func _validate() -> void:
	var resource_path := OS.get_environment("A3GAME_GODOT_PLUGIN_RESOURCE")
	var descriptor_path := OS.get_environment("A3GAME_GODOT_PLUGIN_DESCRIPTOR")
	var report_path := OS.get_environment("A3GAME_GODOT_PLUGIN_REPORT")
	var report := {
		"schema_version": "gamefactory3a.godot.plugin_validation.v1",
		"ok": false,
		"resource_path": resource_path,
		"descriptor_path": descriptor_path,
		"metadata": {},
		"resource_class": "",
		"base_type": "",
		"can_instantiate": false,
		"instantiated": false,
		"instance_class": "",
		"is_tool": false,
		"error": "",
	}
	if resource_path.is_empty() or not resource_path.begins_with("res://addons/"):
		report["error"] = "Plugin entry must be an add-on res:// resource"
	elif descriptor_path.is_empty() or not descriptor_path.begins_with("res://addons/"):
		report["error"] = "Plugin descriptor must be an add-on res:// resource"
	elif descriptor_path.get_file() != "plugin.cfg":
		report["error"] = "Plugin descriptor must be named plugin.cfg"
	else:
		var config := ConfigFile.new()
		var config_error := config.load(descriptor_path)
		if config_error != OK:
			report["error"] = "Plugin descriptor could not be parsed: error %d" % config_error
		else:
			var missing: Array[String] = []
			var wrong_type: Array[String] = []
			for key in ["name", "author", "version", "description", "script"]:
				if not config.has_section_key("plugin", key):
					missing.append(key)
				elif typeof(config.get_value("plugin", key)) != TYPE_STRING:
					wrong_type.append(key)
				else:
					report["metadata"][key] = config.get_value("plugin", key)
			if not missing.is_empty():
				report["error"] = "Plugin descriptor is missing required keys: %s" % ", ".join(missing)
			elif not wrong_type.is_empty():
				report["error"] = "Plugin descriptor keys must be strings: %s" % ", ".join(wrong_type)
			elif str(report["metadata"]["name"]).strip_edges().is_empty():
				report["error"] = "Plugin descriptor name must be non-empty"
			else:
				var descriptor_prefix := descriptor_path.get_base_dir() + "/"
				var configured_resource := descriptor_prefix + str(report["metadata"]["script"])
				if configured_resource != resource_path:
					report["error"] = "Plugin descriptor script does not match the entry resource"
	if str(report["error"]).is_empty():
		var resource = ResourceLoader.load(
			resource_path,
			"Script",
			ResourceLoader.CACHE_MODE_IGNORE,
		)
		if resource == null:
			report["error"] = "Plugin entry script could not be loaded"
		elif not resource is Script:
			report["resource_class"] = resource.get_class()
			report["error"] = "Plugin entry resource is not a Script"
		else:
			var script: Script = resource
			report["resource_class"] = script.get_class()
			report["base_type"] = script.get_instance_base_type()
			report["can_instantiate"] = script.can_instantiate()
			report["is_tool"] = script.is_tool()
			if not report["can_instantiate"]:
				report["error"] = "Plugin entry script cannot be instantiated"
			elif report["base_type"] != "EditorPlugin":
				report["error"] = "Plugin entry script must inherit EditorPlugin"
			elif not report["is_tool"]:
				report["error"] = "Plugin entry script must run in tool mode"
			elif _required_init_arguments(script) > 0:
				report["error"] = (
					"Plugin entry script cannot be instantiated without arguments: "
					+ "_init requires %d argument(s)"
					% _required_init_arguments(script)
				)
			else:
				var instance = script.new()
				if instance == null:
					report["error"] = (
						"Plugin entry script could not be instantiated without arguments"
					)
				elif not instance is EditorPlugin:
					report["instance_class"] = instance.get_class()
					report["error"] = (
						"Plugin entry script did not create an EditorPlugin instance"
					)
				else:
					report["instantiated"] = true
					report["instance_class"] = instance.get_class()
					report["ok"] = true
				if instance != null and is_instance_valid(instance):
					instance.free()
	_write_report(report_path, report)
	quit(0 if bool(report["ok"]) else 1)


func _required_init_arguments(script: Script) -> int:
	for method in script.get_script_method_list():
		if str(method.get("name", "")) != "_init":
			continue
		var arguments: Array = method.get("args", [])
		var defaults: Array = method.get("default_args", [])
		return maxi(0, arguments.size() - defaults.size())
	return 0


func _write_report(path: String, report: Dictionary) -> void:
	if path.is_empty():
		return
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(report, "  "))
