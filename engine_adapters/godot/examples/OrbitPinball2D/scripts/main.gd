extends Node2D


const VIEW_SIZE := Vector2(960.0, 540.0)
const VIOLET := Color("a970ff")
const CYAN := Color("4ffbdf")
const ORANGE := Color("ff9f43")


class Pinball:
	extends RigidBody2D

	func configure() -> void:
		gravity_scale = 0.0
		linear_damp = 0.04
		angular_damp = 0.2
		lock_rotation = true
		can_sleep = false
		continuous_cd = RigidBody2D.CCD_MODE_CAST_RAY
		var physics_material := PhysicsMaterial.new()
		physics_material.bounce = 0.94
		physics_material.friction = 0.03
		physics_material_override = physics_material
		contact_monitor = true
		max_contacts_reported = 12
		collision_layer = 1
		collision_mask = 1
		var collision := CollisionShape2D.new()
		var shape := CircleShape2D.new()
		shape.radius = 12.0
		collision.shape = shape
		add_child(collision)
		queue_redraw()

	func _draw() -> void:
		draw_circle(Vector2.ZERO, 24.0, Color(0.3, 1.0, 0.9, 0.13))
		draw_circle(Vector2.ZERO, 13.0, Color("4ffbdf"))
		draw_circle(Vector2(-4.0, -4.0), 4.0, Color.WHITE)


class Bumper:
	extends StaticBody2D

	var tint := Color.WHITE
	var pulse := 0.0

	func configure(color: Color) -> void:
		tint = color
		collision_layer = 1
		collision_mask = 1
		var collision := CollisionShape2D.new()
		var shape := CircleShape2D.new()
		shape.radius = 31.0
		collision.shape = shape
		add_child(collision)
		queue_redraw()

	func animate(delta: float) -> void:
		pulse = maxf(0.0, pulse - delta * 2.4)
		scale = Vector2.ONE * (1.0 + pulse * 0.22)

	func flash() -> void:
		pulse = 1.0

	func _draw() -> void:
		draw_circle(Vector2.ZERO, 42.0, Color(tint.r, tint.g, tint.b, 0.12))
		draw_circle(Vector2.ZERO, 31.0, Color("171036"))
		draw_arc(Vector2.ZERO, 31.0, 0.0, TAU, 48, tint, 5.0)
		draw_circle(Vector2.ZERO, 18.0, tint)
		draw_circle(Vector2(-5.0, -6.0), 5.0, Color.WHITE)


class Flipper:
	extends AnimatableBody2D

	var tint := Color.WHITE

	func configure(color: Color) -> void:
		tint = color
		collision_layer = 1
		collision_mask = 1
		var collision := CollisionShape2D.new()
		var shape := RectangleShape2D.new()
		shape.size = Vector2(132.0, 22.0)
		collision.shape = shape
		add_child(collision)
		queue_redraw()

	func _draw() -> void:
		draw_circle(Vector2(-58.0, 0.0), 17.0, Color(tint.r, tint.g, tint.b, 0.18))
		draw_rect(Rect2(-66.0, -11.0, 132.0, 22.0), tint, true)
		draw_rect(Rect2(-66.0, -11.0, 132.0, 22.0), Color.WHITE, false, 2.0)


var ball: Pinball
var bumpers: Array[Bumper] = []
var left_flipper: Flipper
var right_flipper: Flipper
var trail := PackedVector2Array()
var score := 0
var lives := 3
var combo := 1
var elapsed := 0.0
var tick := 0
var state_time := 0.0
var game_state := "ORBIT ACTIVE"
var demo_mode := true
var capture_mode := false
var score_label: Label
var state_label: Label


func _ready() -> void:
	demo_mode = not OS.get_cmdline_user_args().has("--manual")
	capture_mode = OS.get_cmdline_user_args().has("--capture")
	_build_table()
	_build_ui()
	spawn_ball()
	queue_redraw()


func _build_table() -> void:
	_create_wall("TopWall", Vector2(480.0, 78.0), Vector2(840.0, 20.0), CYAN)
	_create_wall("LeftWall", Vector2(48.0, 292.0), Vector2(20.0, 408.0), VIOLET)
	_create_wall("RightWall", Vector2(912.0, 292.0), Vector2(20.0, 408.0), VIOLET)
	_create_wall("LowerLeft", Vector2(235.0, 507.0), Vector2(360.0, 18.0), ORANGE)
	_create_wall("LowerRight", Vector2(725.0, 507.0), Vector2(360.0, 18.0), ORANGE)
	_create_wall("SlantLeft", Vector2(168.0, 410.0), Vector2(210.0, 18.0), VIOLET, 0.42)
	_create_wall("SlantRight", Vector2(792.0, 410.0), Vector2(210.0, 18.0), CYAN, -0.42)

	var positions := [
		Vector2(330.0, 185.0), Vector2(480.0, 150.0), Vector2(630.0, 185.0),
		Vector2(405.0, 285.0), Vector2(555.0, 285.0),
	]
	for index in range(positions.size()):
		var bumper := Bumper.new()
		bumper.name = "Bumper_%02d" % index
		bumper.position = positions[index]
		bumper.configure([VIOLET, CYAN, ORANGE][index % 3])
		add_child(bumper)
		bumpers.append(bumper)

	left_flipper = Flipper.new()
	left_flipper.name = "LeftFlipper"
	left_flipper.position = Vector2(392.0, 450.0)
	left_flipper.rotation = -0.25
	left_flipper.configure(CYAN)
	add_child(left_flipper)
	right_flipper = Flipper.new()
	right_flipper.name = "RightFlipper"
	right_flipper.position = Vector2(568.0, 450.0)
	right_flipper.rotation = PI + 0.25
	right_flipper.configure(VIOLET)
	add_child(right_flipper)


func _create_wall(node_name: String, position: Vector2, size: Vector2, color: Color, angle: float = 0.0) -> void:
	var wall := StaticBody2D.new()
	wall.name = node_name
	wall.position = position
	wall.rotation = angle
	wall.collision_layer = 1
	wall.collision_mask = 1
	var collision := CollisionShape2D.new()
	var shape := RectangleShape2D.new()
	shape.size = size
	collision.shape = shape
	wall.add_child(collision)
	var visual := Polygon2D.new()
	visual.polygon = PackedVector2Array([
		Vector2(-size.x * 0.5, -size.y * 0.5),
		Vector2(size.x * 0.5, -size.y * 0.5),
		Vector2(size.x * 0.5, size.y * 0.5),
		Vector2(-size.x * 0.5, size.y * 0.5),
	])
	visual.color = color
	wall.add_child(visual)
	add_child(wall)


func _build_ui() -> void:
	var title := Label.new()
	title.position = Vector2(48.0, 18.0)
	title.text = "ORBIT PINBALL // GRAVITY ARRAY"
	title.add_theme_color_override("font_color", VIOLET)
	title.add_theme_font_size_override("font_size", 25)
	add_child(title)
	score_label = Label.new()
	score_label.position = Vector2(48.0, 51.0)
	score_label.add_theme_color_override("font_color", CYAN)
	score_label.add_theme_font_size_override("font_size", 18)
	add_child(score_label)
	state_label = Label.new()
	state_label.position = Vector2(670.0, 24.0)
	state_label.size = Vector2(240.0, 35.0)
	state_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	state_label.add_theme_color_override("font_color", ORANGE)
	state_label.add_theme_font_size_override("font_size", 19)
	add_child(state_label)
	var controls := Label.new()
	controls.position = Vector2(292.0, 510.0)
	controls.text = "A / ← left flipper     D / → right flipper"
	controls.add_theme_color_override("font_color", Color("c1b5ef"))
	controls.add_theme_font_size_override("font_size", 14)
	add_child(controls)


func spawn_ball() -> void:
	if is_instance_valid(ball):
		ball.queue_free()
	ball = Pinball.new()
	ball.name = "Pinball"
	ball.position = Vector2(480.0, 355.0)
	ball.configure()
	ball.body_entered.connect(_on_ball_body_entered)
	add_child(ball)
	ball.linear_velocity = Vector2(235.0, -310.0)
	trail.clear()


func _physics_process(delta: float) -> void:
	tick += 1
	elapsed += delta
	state_time += delta
	if is_instance_valid(ball):
		var to_core := Vector2(480.0, 260.0) - ball.position
		ball.apply_central_force(to_core.normalized() * 32.0)
		var speed := ball.linear_velocity.length()
		if speed < 225.0:
			ball.linear_velocity = ball.linear_velocity.normalized() * 225.0 if speed > 1.0 else Vector2(220.0, -260.0)
		elif speed > 560.0:
			ball.linear_velocity = ball.linear_velocity.normalized() * 560.0
		trail.append(ball.position)
		if trail.size() > 22:
			trail.remove_at(0)
		if ball.position.y > 565.0 or ball.position.x < -40.0 or ball.position.x > 1000.0:
			lives -= 1
			combo = 1
			if lives <= 0:
				game_state = "ARRAY RESET"
				state_time = 0.0
				lives = 3
			spawn_ball()
	_update_flippers(delta)
	for bumper in bumpers:
		bumper.animate(delta)
	if score >= 5000 and game_state == "ORBIT ACTIVE":
		game_state = "ORBIT SECURED"
		state_time = 0.0
	if game_state != "ORBIT ACTIVE" and state_time > 2.4:
		game_state = "ORBIT ACTIVE"
		score = 0
		combo = 1
	if capture_mode and elapsed > 18.0:
		get_tree().quit()
	_update_ui()
	queue_redraw()


func _update_flippers(delta: float) -> void:
	var left_active := Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT)
	var right_active := Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT)
	if demo_mode and is_instance_valid(ball):
		left_active = ball.position.y > 360.0 and ball.position.x < 520.0
		right_active = ball.position.y > 360.0 and ball.position.x > 440.0
	var left_target := -0.82 if left_active else -0.24
	var right_target := PI + (0.82 if right_active else 0.24)
	left_flipper.rotation = lerp_angle(left_flipper.rotation, left_target, min(1.0, delta * 16.0))
	right_flipper.rotation = lerp_angle(right_flipper.rotation, right_target, min(1.0, delta * 16.0))


func _on_ball_body_entered(body: Node) -> void:
	if body is Bumper:
		var bumper := body as Bumper
		bumper.flash()
		combo = mini(8, combo + 1)
		score += 125 * combo
		var kick := (ball.position - bumper.position).normalized()
		ball.apply_central_impulse(kick * 72.0)
	elif body == left_flipper or body == right_flipper:
		score += 40 * combo


func _update_ui() -> void:
	score_label.text = "SCORE %06d     COMBO x%d     BALLS %d" % [score, combo, lives]
	state_label.text = game_state


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, VIEW_SIZE), Color("100824"))
	for radius in [78.0, 132.0, 190.0]:
		draw_arc(Vector2(480.0, 270.0), radius, 0.0, TAU, 96, Color(0.35, 0.22, 0.72, 0.12), 2.0)
	for index in range(36):
		var angle := TAU * float(index) / 36.0 + float(tick) * 0.002
		var radius := 100.0 + float((index * 37) % 330)
		var star := Vector2(480.0, 270.0) + Vector2(cos(angle), sin(angle)) * radius
		draw_circle(star, 1.5, Color(0.72, 0.86, 1.0, 0.42))
	for index in range(trail.size()):
		var alpha := float(index + 1) / float(maxi(1, trail.size())) * 0.34
		draw_circle(trail[index], 3.0 + alpha * 10.0, Color(0.31, 0.98, 0.87, alpha))
	draw_rect(Rect2(38.0, 68.0, 884.0, 448.0), Color(0.45, 0.28, 0.95, 0.65), false, 2.0)


func smoke_snapshot() -> Dictionary:
	return {
		"game": "orbit_pinball_2d",
		"tick": tick,
		"ball": [ball.position.x, ball.position.y],
		"velocity": [ball.linear_velocity.x, ball.linear_velocity.y],
		"bumpers": bumpers.size(),
		"flippers": 2,
		"score": score,
		"state": game_state,
	}
