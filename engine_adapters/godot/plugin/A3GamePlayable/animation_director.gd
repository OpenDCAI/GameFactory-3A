class_name A3GameAnimationDirector
extends RefCounted


static func find_player(root: Node) -> AnimationPlayer:
	if root == null:
		return null
	if root is AnimationPlayer:
		return root as AnimationPlayer
	var players := root.find_children("*", "AnimationPlayer", true, false)
	return players[0] as AnimationPlayer if not players.is_empty() else null


static func play(root: Node, animation_name: StringName, blend_seconds: float = 0.15, speed: float = 1.0) -> Dictionary:
	var player := find_player(root)
	if player == null:
		return {"ok": false, "error": "no AnimationPlayer exists below root"}
	if not player.has_animation(animation_name):
		return {"ok": false, "error": "animation does not exist: " + str(animation_name)}
	if not is_finite(blend_seconds) or blend_seconds < 0.0:
		return {"ok": false, "error": "blend_seconds must be finite and non-negative"}
	if not is_finite(speed) or is_zero_approx(speed):
		return {"ok": false, "error": "speed must be finite and non-zero"}
	player.play(animation_name, blend_seconds, speed)
	return {"ok": true, "animation": str(animation_name), "speed": speed}


static func stop(root: Node, keep_state: bool = false) -> Dictionary:
	var player := find_player(root)
	if player == null:
		return {"ok": false, "error": "no AnimationPlayer exists below root"}
	player.stop(keep_state)
	return {"ok": true}
