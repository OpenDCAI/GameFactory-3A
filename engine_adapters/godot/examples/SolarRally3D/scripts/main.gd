extends Node3D


const ROAD_RADIUS := 14.0
const CYAN := Color("35e7ff")
const ORANGE := Color("ff8c42")
const PURPLE := Color("8c5bff")


class RallyVehicle:
	extends CharacterBody3D

	var drive_speed := 0.0
	var visual_roll := 0.0
	var body_visual: Node3D

	func configure() -> void:
		collision_layer = 1
		collision_mask = 1
		var collision := CollisionShape3D.new()
		var box_shape := BoxShape3D.new()
		box_shape.size = Vector3(1.45, 0.65, 2.7)
		collision.shape = box_shape
		collision.position.y = 0.52
		add_child(collision)

		body_visual = Node3D.new()
		body_visual.name = "VehicleVisual"
		add_child(body_visual)
		var shell := MeshInstance3D.new()
		var shell_mesh := BoxMesh.new()
		shell_mesh.size = Vector3(1.5, 0.55, 2.75)
		shell.mesh = shell_mesh
		shell.position.y = 0.56
		var shell_material := StandardMaterial3D.new()
		shell_material.albedo_color = Color("ff5d73")
		shell_material.metallic = 0.7
		shell_material.roughness = 0.22
		shell_material.emission_enabled = true
		shell_material.emission = Color("4a1024")
		shell.material_override = shell_material
		body_visual.add_child(shell)

		var canopy := MeshInstance3D.new()
		var canopy_mesh := BoxMesh.new()
		canopy_mesh.size = Vector3(1.1, 0.48, 1.25)
		canopy.mesh = canopy_mesh
		canopy.position = Vector3(0.0, 1.03, -0.18)
		var glass := StandardMaterial3D.new()
		glass.albedo_color = Color("173f6f")
		glass.metallic = 0.25
		glass.roughness = 0.08
		glass.emission_enabled = true
		glass.emission = Color("0b294d")
		canopy.material_override = glass
		body_visual.add_child(canopy)

		for side in [-1.0, 1.0]:
			for z_value in [-0.86, 0.88]:
				var wheel := MeshInstance3D.new()
				var wheel_mesh := CylinderMesh.new()
				wheel_mesh.top_radius = 0.3
				wheel_mesh.bottom_radius = 0.3
				wheel_mesh.height = 0.24
				wheel.mesh = wheel_mesh
				wheel.position = Vector3(side * 0.84, 0.35, z_value)
				wheel.rotation_degrees.z = 90.0
				var rubber := StandardMaterial3D.new()
				rubber.albedo_color = Color("101426")
				rubber.roughness = 0.85
				wheel.material_override = rubber
				body_visual.add_child(wheel)

		for side in [-1.0, 1.0]:
			var thruster := OmniLight3D.new()
			thruster.light_color = Color("35e7ff")
			thruster.light_energy = 2.2
			thruster.omni_range = 2.7
			thruster.position = Vector3(side * 0.48, 0.48, 1.5)
			body_visual.add_child(thruster)

	func drive_toward(direction: Vector3, throttle: float, delta: float) -> void:
		var flat_direction := Vector3(direction.x, 0.0, direction.z).normalized()
		if flat_direction.length_squared() > 0.01:
			var desired_yaw := atan2(-flat_direction.x, -flat_direction.z)
			var yaw_error := wrapf(desired_yaw - rotation.y, -PI, PI)
			rotation.y += clamp(yaw_error, -delta * 2.6, delta * 2.6)
			visual_roll = lerpf(visual_roll, clamp(-yaw_error * 0.22, -0.24, 0.24), min(1.0, delta * 5.0))
		drive_speed = move_toward(drive_speed, 14.5 * throttle, delta * 8.0)
		var forward := -global_transform.basis.z
		velocity.x = forward.x * drive_speed
		velocity.z = forward.z * drive_speed
		if not is_on_floor():
			velocity.y -= 24.0 * delta
		else:
			velocity.y = -0.5
		move_and_slide()
		body_visual.rotation.z = visual_roll
		body_visual.position.y = sin(Time.get_ticks_msec() * 0.009) * 0.035


var vehicle: RallyVehicle
var chase_camera: Camera3D
var checkpoint_areas: Array[Area3D] = []
var checkpoint_visuals: Array[Node3D] = []
var waypoints: Array[Vector3] = []
var current_checkpoint := 0
var lap := 1
var elapsed := 0.0
var tick := 0
var demo_mode := true
var capture_mode := false
var race_state := "RACING"
var status_label: Label
var telemetry_label: Label


func _ready() -> void:
	demo_mode = not OS.get_cmdline_user_args().has("--manual")
	capture_mode = OS.get_cmdline_user_args().has("--capture")
	_build_environment()
	_build_track()
	_build_ui()
	vehicle = RallyVehicle.new()
	vehicle.name = "RallyVehicle"
	vehicle.position = Vector3(-5.0, 0.45, 13.0)
	vehicle.configure()
	add_child(vehicle)
	chase_camera = Camera3D.new()
	chase_camera.name = "ChaseCamera"
	chase_camera.current = true
	chase_camera.fov = 67.0
	add_child(chase_camera)
	_update_camera(1.0)
	_update_ui()


func _build_environment() -> void:
	var world := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("071129")
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color("7188bb")
	environment.ambient_light_energy = 0.72
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.glow_enabled = true
	world.environment = environment
	add_child(world)

	var sun := DirectionalLight3D.new()
	sun.name = "SolarKeyLight"
	sun.rotation_degrees = Vector3(-56.0, -24.0, 0.0)
	sun.light_color = Color("fff2d0")
	sun.light_energy = 1.25
	sun.shadow_enabled = true
	add_child(sun)

	var fill := OmniLight3D.new()
	fill.position = Vector3(0.0, 9.0, 0.0)
	fill.light_color = PURPLE
	fill.light_energy = 7.0
	fill.omni_range = 28.0
	add_child(fill)


func _material(color: Color, emission_strength: float = 0.0, metallic: float = 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.42
	material.metallic = metallic
	if emission_strength > 0.0:
		material.emission_enabled = true
		material.emission = color * emission_strength
	return material


func _mesh_box(parent: Node, node_name: String, size: Vector3, position: Vector3, material: Material) -> MeshInstance3D:
	var mesh_instance := MeshInstance3D.new()
	mesh_instance.name = node_name
	var mesh := BoxMesh.new()
	mesh.size = size
	mesh_instance.mesh = mesh
	mesh_instance.position = position
	mesh_instance.material_override = material
	parent.add_child(mesh_instance)
	return mesh_instance


func _static_box(node_name: String, size: Vector3, position: Vector3, material: Material) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = node_name
	body.position = position
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	collision.shape = shape
	body.add_child(collision)
	_mesh_box(body, "Visual", size, Vector3.ZERO, material)
	add_child(body)
	return body


func _build_track() -> void:
	_static_box("TrackFoundation", Vector3(46.0, 0.5, 40.0), Vector3(0.0, -0.28, 0.0), _material(Color("141a2d"), 0.0, 0.1))
	for index in range(40):
		var angle := TAU * float(index) / 40.0
		var segment := _mesh_box(
			self,
			"Road_%02d" % index,
			Vector3(5.0, 0.11, 3.6),
			Vector3(cos(angle) * ROAD_RADIUS, 0.04, sin(angle) * ROAD_RADIUS),
			_material(Color("2c3554") if index % 2 == 0 else Color("252e49"), 0.0, 0.35)
		)
		segment.rotation.y = -angle
		var marker := _mesh_box(
			segment,
			"LaneMarker",
			Vector3(0.12, 0.04, 1.1),
			Vector3(0.0, 0.09, 0.0),
			_material(CYAN if index % 2 == 0 else ORANGE, 1.8)
		)
		marker.rotation.y = 0.0

	var solar_core := MeshInstance3D.new()
	var core_mesh := SphereMesh.new()
	core_mesh.radius = 3.3
	core_mesh.height = 6.6
	solar_core.mesh = core_mesh
	solar_core.position = Vector3(0.0, 3.2, 0.0)
	solar_core.material_override = _material(Color("ffb43a"), 3.0, 0.1)
	add_child(solar_core)

	for index in range(12):
		var tower_angle := TAU * float(index) / 12.0
		var tower_pos := Vector3(cos(tower_angle) * 22.0, 1.2, sin(tower_angle) * 19.0)
		_static_box(
			"Barrier_%02d" % index,
			Vector3(1.3, 2.5, 1.3),
			tower_pos,
			_material(PURPLE if index % 2 == 0 else CYAN, 0.8, 0.25)
		)

	waypoints = [
		Vector3(0.0, 0.5, 14.0),
		Vector3(-10.0, 0.5, 10.0),
		Vector3(-14.0, 0.5, 0.0),
		Vector3(-10.0, 0.5, -10.0),
		Vector3(0.0, 0.5, -14.0),
		Vector3(10.0, 0.5, -10.0),
		Vector3(14.0, 0.5, 0.0),
		Vector3(10.0, 0.5, 10.0),
	]
	for index in range(waypoints.size()):
		_create_checkpoint(index, waypoints[index])


func _create_checkpoint(index: int, location: Vector3) -> void:
	var area := Area3D.new()
	area.name = "Checkpoint_%02d" % index
	area.position = location
	area.collision_layer = 2
	area.collision_mask = 1
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(4.5, 3.0, 4.5)
	collision.shape = shape
	area.add_child(collision)
	area.body_entered.connect(_on_checkpoint_entered.bind(index))
	add_child(area)
	checkpoint_areas.append(area)

	var visual := Node3D.new()
	visual.name = "CheckpointVisual_%02d" % index
	visual.position = location
	var color := CYAN if index == 0 else PURPLE
	var glow := _material(color, 2.1, 0.25)
	_mesh_box(visual, "LeftPylon", Vector3(0.32, 3.6, 0.32), Vector3(-2.0, 1.8, 0.0), glow)
	_mesh_box(visual, "RightPylon", Vector3(0.32, 3.6, 0.32), Vector3(2.0, 1.8, 0.0), glow)
	_mesh_box(visual, "Header", Vector3(4.3, 0.25, 0.25), Vector3(0.0, 3.5, 0.0), glow)
	visual.rotation.y = atan2(location.x, location.z)
	add_child(visual)
	checkpoint_visuals.append(visual)


func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var panel := ColorRect.new()
	panel.position = Vector2(26.0, 22.0)
	panel.size = Vector2(382.0, 92.0)
	panel.color = Color(0.025, 0.045, 0.11, 0.84)
	layer.add_child(panel)
	var title := Label.new()
	title.position = Vector2(42.0, 31.0)
	title.text = "SOLAR RALLY // HELIOS CIRCUIT"
	title.add_theme_color_override("font_color", ORANGE)
	title.add_theme_font_size_override("font_size", 22)
	layer.add_child(title)
	telemetry_label = Label.new()
	telemetry_label.position = Vector2(42.0, 66.0)
	telemetry_label.add_theme_color_override("font_color", CYAN)
	telemetry_label.add_theme_font_size_override("font_size", 18)
	layer.add_child(telemetry_label)
	status_label = Label.new()
	status_label.position = Vector2(690.0, 28.0)
	status_label.size = Vector2(230.0, 40.0)
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	status_label.add_theme_color_override("font_color", Color.WHITE)
	status_label.add_theme_font_size_override("font_size", 22)
	layer.add_child(status_label)
	var controls := Label.new()
	controls.position = Vector2(42.0, 506.0)
	controls.text = "WASD / arrows to steer  •  pass gates in sequence  •  two laps to win"
	controls.add_theme_color_override("font_color", Color("b5c8eb"))
	controls.add_theme_font_size_override("font_size", 14)
	layer.add_child(controls)


func _physics_process(delta: float) -> void:
	tick += 1
	elapsed += delta
	var direction := -vehicle.global_transform.basis.z
	var throttle := 1.0
	if demo_mode:
		var target := waypoints[current_checkpoint]
		direction = target - vehicle.global_position
	else:
		var steering := float(Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT)) - float(Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT))
		var current_forward := -vehicle.global_transform.basis.z
		direction = current_forward.rotated(Vector3.UP, steering * 0.75)
		throttle = float(Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP)) - 0.45 * float(Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN))
	vehicle.drive_toward(direction, throttle, delta)
	if vehicle.position.y < -3.0 or vehicle.position.length() > 42.0:
		vehicle.position = waypoints[(current_checkpoint - 1 + waypoints.size()) % waypoints.size()] + Vector3.UP
		vehicle.velocity = Vector3.ZERO
	_update_camera(delta)
	for index in range(checkpoint_visuals.size()):
		var active := index == current_checkpoint
		var pulse := 1.0 + sin(elapsed * 4.0 + float(index)) * (0.12 if active else 0.035)
		checkpoint_visuals[index].scale = Vector3(pulse, pulse, pulse)
	if capture_mode and elapsed > 18.0:
		get_tree().quit()
	_update_ui()


func _on_checkpoint_entered(body: Node3D, index: int) -> void:
	if body != vehicle or index != current_checkpoint or race_state != "RACING":
		return
	current_checkpoint += 1
	if current_checkpoint >= waypoints.size():
		current_checkpoint = 0
		lap += 1
		if lap > 2:
			race_state = "CHAMPION"
			lap = 1


func _update_camera(delta: float) -> void:
	var behind := vehicle.global_transform.basis.z * 8.5
	var desired := vehicle.global_position + behind + Vector3.UP * 5.8
	chase_camera.global_position = chase_camera.global_position.lerp(desired, min(1.0, delta * 4.2))
	chase_camera.look_at(vehicle.global_position + Vector3.UP * 0.8, Vector3.UP)


func _update_ui() -> void:
	telemetry_label.text = "LAP %d/2   GATE %02d/08   SPEED %03d" % [
		lap,
		current_checkpoint + 1,
		int(abs(vehicle.drive_speed) * 12.0),
	]
	status_label.text = race_state


func smoke_snapshot() -> Dictionary:
	return {
		"game": "solar_rally_3d",
		"tick": tick,
		"vehicle": [vehicle.position.x, vehicle.position.y, vehicle.position.z],
		"speed": vehicle.drive_speed,
		"checkpoint": current_checkpoint,
		"checkpoint_count": checkpoint_areas.size(),
		"lap": lap,
		"state": race_state,
	}
