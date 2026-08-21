class_name A3GameIdentity
extends Node


@export var world_id := "world_001"
@export var participant_id := ""
@export var entity_id := ""


func configure(values: Dictionary) -> Dictionary:
	var next_world := str(values.get("world_id", "world_001")).strip_edges()
	var next_participant := str(values.get("participant_id", "")).strip_edges()
	var next_entity := str(values.get("entity_id", "")).strip_edges()
	if next_world.is_empty():
		return {"ok": false, "error": "world_id is required"}
	if next_entity.is_empty():
		return {"ok": false, "error": "entity_id is required"}
	world_id = next_world
	participant_id = next_participant
	entity_id = next_entity
	return {"ok": true, "identity": snapshot()}


func snapshot() -> Dictionary:
	return {
		"world_id": world_id,
		"participant_id": participant_id,
		"entity_id": entity_id,
	}
