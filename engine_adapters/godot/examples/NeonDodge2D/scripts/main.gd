extends Node2D


const VIEW_SIZE := Vector2(960.0, 540.0)
const PLAY_AREA := Rect2(42.0, 84.0, 876.0, 420.0)
const CYAN := Color("52f7ff")
const MAGENTA := Color("ff4fd8")
const GOLD := Color("ffd166")


class Pilot:
	extends CharacterBody2D

	func _ready() -> void:
		var shape := CollisionShape2D.new()
		var circle := CircleShape2D.new()
		circle.radius = 16.0
		shape.shape = circle
		add_child(shape)
		queue_redraw()

	func _draw() -> void:
		draw_circle(Vector2.ZERO, 23.0, Color(0.1, 0.95, 1.0, 0.12))
		draw_colored_polygon(
			PackedVector2Array([
				Vector2(0.0, -20.0),
				Vector2(15.0, 15.0),
				Vector2(0.0, 9.0),
				Vector2(-15.0, 15.0),
			]),
			Color("52f7ff")
		)
		draw_polyline(
			PackedVector2Array([
				Vector2(0.0, -20.0),
				Vector2(15.0, 15.0),
				Vector2(0.0, 9.0),
				Vector2(-15.0, 15.0),
				Vector2(0.0, -20.0),
			]),
			Color.WHITE,
			2.0
		)
		draw_line(Vector2(-7.0, 17.0), Vector2(-4.0, 31.0), Color("ff4fd8"), 4.0)
		draw_line(Vector2(7.0, 17.0), Vector2(4.0, 31.0), Color("ff4fd8"), 4.0)


class FallingArea:
	extends Area2D

	var speed := 140.0
	var phase := 0.0
	var collectible := false

	func configure(is_collectible: bool, start_phase: float) -> void:
		collectible = is_collectible
		phase = start_phase
		collision_layer = 2 if collectible else 4
		collision_mask = 1
		var shape := CollisionShape2D.new()
		var circle := CircleShape2D.new()
		circle.radius = 12.0 if collectible else 18.0
		shape.shape = circle
		add_child(shape)
		queue_redraw()

	func animate(delta: float) -> void:
		position.y += speed * delta
		rotation += delta * (2.4 if collectible else 1.15)
		if collectible:
			scale = Vector2.ONE * (1.0 + sin(Time.get_ticks_msec() * 0.006 + phase) * 0.16)

	func _draw() -> void:
		if collectible:
			draw_circle(Vector2.ZERO, 20.0, Color(1.0, 0.82, 0.25, 0.16))
			draw_colored_polygon(
				PackedVector2Array([
					Vector2(0.0, -12.0), Vector2(10.0, 0.0),
					Vector2(0.0, 12.0), Vector2(-10.0, 0.0),
				]),
				Color("ffd166")
			)
		else:
			draw_circle(Vector2.ZERO, 27.0, Color(1.0, 0.18, 0.45, 0.12))
			draw_colored_polygon(
				PackedVector2Array([
					Vector2(-18.0, -9.0), Vector2(-3.0, -20.0),
					Vector2(17.0, -11.0), Vector2(20.0, 8.0),
					Vector2(2.0, 19.0), Vector2(-19.0, 10.0),
				]),
				Color("ff356f")
			)
			draw_circle(Vector2(-5.0, -4.0), 5.0, Color("741f5b"))


var pilot: Pilot
var hazards: Array[FallingArea] = []
var pickups: Array[FallingArea] = []
var score_label: Label
var status_label: Label
var instruction_label: Label
var score := 0
var shield := 3
var elapsed := 0.0
var state_time := 0.0
var capture_elapsed := 0.0
var tick := 0
var game_state := "PLAYING"
var demo_mode := true
var capture_mode := false
var stars := PackedVector2Array()


func _ready() -> void:
	demo_mode = not OS.get_cmdline_user_args().has("--manual")
	capture_mode = OS.get_cmdline_user_args().has("--capture")
	for index in range(86):
		stars.append(Vector2(
			23.0 + float((index * 83) % 914),
			72.0 + float((index * 47) % 438)
		))
	_create_ui()
	pilot = Pilot.new()
	pilot.name = "Pilot"
	pilot.position = Vector2(480.0, 430.0)
	pilot.collision_layer = 1
	pilot.collision_mask = 0
	add_child(pilot)
	for index in range(8):
		var hazard := FallingArea.new()
		hazard.name = "Meteor_%02d" % index
		hazard.configure(false, float(index))
		hazard.speed = 112.0 + float((index * 29) % 92)
		hazard.position = Vector2(92.0 + float((index * 127) % 780), 98.0 - float(index * 73))
		hazard.body_entered.connect(_on_hazard_body_entered.bind(hazard))
		add_child(hazard)
		hazards.append(hazard)
	for index in range(5):
		var pickup := FallingArea.new()
		pickup.name = "Energy_%02d" % index
		pickup.configure(true, float(index) * 0.7)
		pickup.speed = 82.0 + float(index * 7)
		pickup.position = Vector2(140.0 + float((index * 173) % 690), 120.0 - float(index * 104))
		pickup.body_entered.connect(_on_pickup_body_entered.bind(pickup))
		add_child(pickup)
		pickups.append(pickup)
	_update_ui()
	queue_redraw()


func _create_ui() -> void:
	var title := Label.new()
	title.position = Vector2(42.0, 20.0)
	title.text = "NEON DODGE // SECTOR 07"
	title.add_theme_color_override("font_color", CYAN)
	title.add_theme_font_size_override("font_size", 26)
	add_child(title)

	score_label = Label.new()
	score_label.position = Vector2(42.0, 54.0)
	score_label.add_theme_color_override("font_color", GOLD)
	score_label.add_theme_font_size_override("font_size", 18)
	add_child(score_label)

	status_label = Label.new()
	status_label.position = Vector2(674.0, 23.0)
	status_label.size = Vector2(244.0, 34.0)
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	status_label.add_theme_color_override("font_color", MAGENTA)
	status_label.add_theme_font_size_override("font_size", 20)
	add_child(status_label)

	instruction_label = Label.new()
	instruction_label.position = Vector2(42.0, 505.0)
	instruction_label.text = "WASD / arrows to weave  •  gold shards restore shields  •  survive 14 seconds"
	instruction_label.add_theme_color_override("font_color", Color("98a6d4"))
	instruction_label.add_theme_font_size_override("font_size", 14)
	add_child(instruction_label)


func _physics_process(delta: float) -> void:
	tick += 1
	elapsed += delta
	state_time += delta
	capture_elapsed += delta
	if game_state == "PLAYING":
		_drive_pilot(delta)
		for index in range(hazards.size()):
			var hazard := hazards[index]
			hazard.animate(delta)
			if hazard.position.y > 530.0:
				_recycle(hazard, index, false)
		for index in range(pickups.size()):
			var pickup := pickups[index]
			pickup.animate(delta)
			if pickup.position.y > 528.0:
				_recycle(pickup, index, true)
		if elapsed >= 14.0:
			_set_state("SECTOR CLEAR")
	else:
		for hazard in hazards:
			hazard.rotation += delta
		if state_time > 2.2:
			_reset_round()
	if capture_mode and capture_elapsed > 18.0:
		get_tree().quit()
	_update_ui()
	queue_redraw()


func _drive_pilot(delta: float) -> void:
	var direction := Vector2.ZERO
	if demo_mode:
		var target := Vector2(
			480.0 + sin(elapsed * 1.27) * 315.0,
			405.0 + sin(elapsed * 0.73) * 44.0
		)
		direction = (target - pilot.position).limit_length(1.0)
	else:
		direction = Vector2(
			float(Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT))
				- float(Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT)),
			float(Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN))
				- float(Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP))
		).normalized()
	pilot.velocity = pilot.velocity.lerp(direction * 305.0, min(1.0, delta * 8.0))
	pilot.move_and_slide()
	pilot.position.x = clamp(pilot.position.x, PLAY_AREA.position.x + 20.0, PLAY_AREA.end.x - 20.0)
	pilot.position.y = clamp(pilot.position.y, PLAY_AREA.position.y + 20.0, PLAY_AREA.end.y - 20.0)
	pilot.rotation = lerp_angle(pilot.rotation, direction.x * 0.25, min(1.0, delta * 9.0))


func _on_hazard_body_entered(body: Node, hazard: FallingArea) -> void:
	if body != pilot or game_state != "PLAYING":
		return
	shield -= 1
	_recycle(hazard, hazards.find(hazard), false)
	if shield <= 0:
		_set_state("SIGNAL LOST")


func _on_pickup_body_entered(body: Node, pickup: FallingArea) -> void:
	if body != pilot or game_state != "PLAYING":
		return
	score += 250
	shield = min(5, shield + 1)
	_recycle(pickup, pickups.find(pickup), true)


func _recycle(area: FallingArea, index: int, collectible: bool) -> void:
	var safe_index: int = maxi(0, index)
	area.position = Vector2(
		80.0 + float((safe_index * 181 + tick * 7) % 800),
		-55.0 - float((safe_index * 89 + tick * 3) % 300)
	)
	area.speed = (78.0 if collectible else 118.0) + float((safe_index * 31 + tick) % 74)


func _set_state(next_state: String) -> void:
	game_state = next_state
	state_time = 0.0
	pilot.velocity = Vector2.ZERO


func _reset_round() -> void:
	game_state = "PLAYING"
	state_time = 0.0
	elapsed = 0.0
	shield = 3
	pilot.position = Vector2(480.0, 430.0)
	for index in range(hazards.size()):
		_recycle(hazards[index], index, false)
	for index in range(pickups.size()):
		_recycle(pickups[index], index, true)


func _update_ui() -> void:
	score_label.text = "SCORE %06d     SHIELDS %s     TIME %04.1f" % [
		score,
		"◆".repeat(max(0, shield)),
		max(0.0, 14.0 - elapsed),
	]
	status_label.text = game_state


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, VIEW_SIZE), Color("07102d"))
	for star in stars:
		var flicker := 0.35 + sin(float(tick) * 0.025 + star.x * 0.02) * 0.18
		draw_circle(star, 1.3, Color(0.5, 0.75, 1.0, flicker))
	for x in range(42, 919, 73):
		draw_line(Vector2(x, 84.0), Vector2(x, 504.0), Color(0.12, 0.42, 0.7, 0.12), 1.0)
	for y in range(84, 505, 52):
		draw_line(Vector2(42.0, y), Vector2(918.0, y), Color(0.12, 0.42, 0.7, 0.12), 1.0)
	draw_rect(PLAY_AREA, Color(0.32, 0.95, 1.0, 0.5), false, 2.0)
	if game_state != "PLAYING":
		draw_rect(Rect2(250.0, 220.0, 460.0, 95.0), Color(0.02, 0.03, 0.12, 0.92), true)
		draw_rect(Rect2(250.0, 220.0, 460.0, 95.0), MAGENTA, false, 3.0)


func smoke_snapshot() -> Dictionary:
	return {
		"game": "neon_dodge_2d",
		"tick": tick,
		"player": [pilot.position.x, pilot.position.y],
		"hazards": hazards.size(),
		"pickups": pickups.size(),
		"state": game_state,
		"score": score,
	}
