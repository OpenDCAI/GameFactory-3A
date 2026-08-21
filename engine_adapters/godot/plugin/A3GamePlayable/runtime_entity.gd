class_name A3GameRuntimeEntity
extends Node3D


signal runtime_input(input_state: Dictionary)
signal input_rejected(error: String)

const INPUT_STATE = preload("res://addons/a3game_playable/input_state.gd")

@export var a3game_entity_id := ""
@export var a3game_world_id := "world_001"
@export var a3game_participant_id := ""
var last_input: Dictionary = {}


func _ready() -> void:
	add_to_group("a3game_runtime_entity")


func apply_a3game_input(input_state: Dictionary) -> void:
	var normalized: Dictionary = INPUT_STATE.normalize(input_state)
	if normalized.get("ok") != true:
		input_rejected.emit(str(normalized.get("error", "invalid input state")))
		return
	last_input = Dictionary(normalized["input"]).duplicate(true)
	runtime_input.emit(last_input)


func configure_a3game_identity(identity: Dictionary) -> Dictionary:
	var entity_id := str(identity.get("entity_id", "")).strip_edges()
	var world_id := str(identity.get("world_id", "world_001")).strip_edges()
	if entity_id.is_empty():
		return {"ok": false, "error": "entity_id is required"}
	if world_id.is_empty():
		return {"ok": false, "error": "world_id is required"}
	a3game_entity_id = entity_id
	a3game_world_id = world_id
	a3game_participant_id = str(identity.get("participant_id", "")).strip_edges()
	return {"ok": true, "identity": a3game_snapshot()}


func a3game_snapshot() -> Dictionary:
	return {
		"entity_id": a3game_entity_id,
		"world_id": a3game_world_id,
		"participant_id": a3game_participant_id,
		"last_input": last_input.duplicate(true),
	}


func clear_a3game_entity() -> void:
	queue_free()
