extends Node


signal session_joined(session: Dictionary)
signal session_reconnected(previous_session: Dictionary, session: Dictionary)
signal session_left(session: Dictionary)
signal input_received(entity_id: String, input_state: Dictionary)
signal world_reset(world_id: String)

const DEFAULT_PORT := 30050
const DEFAULT_HOST := "127.0.0.1"
const DEFAULT_WORLD_ID := "world_001"
const INPUT_STATE = preload("res://addons/a3game_playable/input_state.gd")

var _peer := PacketPeerUDP.new()
var _sessions: Dictionary = {}
var _last_inputs: Dictionary = {}
var _port := DEFAULT_PORT
var _host := DEFAULT_HOST


func _ready() -> void:
	var configured_host := OS.get_environment("A3GAME_GODOT_RUNTIME_HOST")
	if not configured_host.is_empty():
		_host = configured_host
	elif ProjectSettings.has_setting("a3game/runtime_host"):
		_host = str(ProjectSettings.get_setting("a3game/runtime_host"))
	var configured := OS.get_environment("A3GAME_GODOT_RUNTIME_PORT")
	if not configured.is_empty() and configured.is_valid_int():
		_port = int(configured)
	elif ProjectSettings.has_setting("a3game/runtime_port"):
		_port = int(ProjectSettings.get_setting("a3game/runtime_port"))
	var error := _peer.bind(_port, _host)
	if error != OK:
		push_error("A3GameRuntime could not bind UDP endpoint %s:%d: %s" % [_host, _port, error_string(error)])
		set_process(false)


func _exit_tree() -> void:
	_peer.close()


func _process(_delta: float) -> void:
	while _peer.get_available_packet_count() > 0:
		var packet := _peer.get_packet()
		var remote_host := _peer.get_packet_ip()
		var remote_port := _peer.get_packet_port()
		var parsed = JSON.parse_string(packet.get_string_from_utf8())
		var response: Dictionary
		if parsed is Dictionary:
			response = _handle_message(parsed)
		else:
			response = {"ok": false, "error": "Runtime packet must be a JSON object"}
		response["request_id"] = str(parsed.get("request_id", "")) if parsed is Dictionary else ""
		_peer.set_dest_address(remote_host, remote_port)
		_peer.put_packet(JSON.stringify(response).to_utf8_buffer())


func find_entity(entity_id: String) -> Node:
	var normalized_entity_id := str(entity_id)
	if normalized_entity_id.is_empty():
		return null
	for node in get_tree().get_nodes_in_group("a3game_runtime_entity"):
		if str(node.get("a3game_entity_id")) == normalized_entity_id:
			return node
	return null


func _handle_message(message: Dictionary) -> Dictionary:
	var operation := str(message.get("operation", ""))
	if operation == "status":
		return {
			"ok": true,
			"operation": operation,
			"host": _host,
			"port": _port,
			"sessions": _sessions.size(),
			"capabilities": ["identity", "normalized_input", "world_sessions", "entity_binding"],
		}
	if operation == "session.join":
		var controller_id := str(message.get("controller_id", ""))
		if controller_id.is_empty():
			return {"ok": false, "operation": operation, "error": "controller_id is required"}
		var participant_id := str(message.get("participant_id", ""))
		if participant_id.is_empty():
			return {"ok": false, "operation": operation, "error": "participant_id is required"}
		var entity_id := str(message.get("entity_id", ""))
		if entity_id.is_empty():
			return {"ok": false, "operation": operation, "error": "entity_id is required"}
		var controllers_to_replace: Array = []
		var previous_session: Dictionary = {}
		for existing_controller_id in _sessions:
			var existing: Dictionary = _sessions[existing_controller_id]
			if str(existing.get("participant_id", "")) != participant_id:
				continue
			if str(existing.get("entity_id", "")) != entity_id:
				return {
					"ok": false,
					"operation": operation,
					"error": "participant_id is already bound to another entity_id",
				}
			if str(existing_controller_id) != controller_id:
				controllers_to_replace.append(existing_controller_id)
		for old_controller_id in controllers_to_replace:
			var previous: Dictionary = _sessions.get(old_controller_id, {})
			previous_session = previous
			_sessions.erase(old_controller_id)
		_sessions[controller_id] = message.duplicate(true)
		var entity_reused := find_entity(entity_id) != null
		if not controllers_to_replace.is_empty() or entity_reused:
			session_reconnected.emit(previous_session, _sessions[controller_id])
		else:
			session_joined.emit(_sessions[controller_id])
		return {
			"ok": true,
			"operation": operation,
			"participant_id": participant_id,
			"controller_id": controller_id,
			"entity_id": entity_id,
			"replaced_controllers": controllers_to_replace.size(),
			"entity_reused": entity_reused,
			"sessions": _sessions.size(),
		}
	if operation == "session.leave":
		var controller_id := str(message.get("controller_id", ""))
		var previous: Dictionary = _sessions.get(controller_id, {})
		_sessions.erase(controller_id)
		session_left.emit(previous)
		return {"ok": true, "operation": operation, "controller_id": controller_id}
	if operation == "session.input":
		var controller_id := str(message.get("controller_id", ""))
		if not _sessions.has(controller_id):
			return {"ok": false, "operation": operation, "error": "Unknown controller_id"}
		var session: Dictionary = _sessions[controller_id]
		var entity_id := str(session.get("entity_id", ""))
		var normalized_input: Dictionary = INPUT_STATE.normalize(message.get("input", {}))
		if normalized_input.get("ok") != true:
			return {
				"ok": false,
				"operation": operation,
				"error": normalized_input.get("error", "invalid input state"),
			}
		var input_state: Dictionary = normalized_input["input"]
		_last_inputs[entity_id] = input_state.duplicate(true)
		input_received.emit(entity_id, input_state)
		for node in get_tree().get_nodes_in_group("a3game_runtime_entity"):
			if str(node.get("a3game_entity_id")) == entity_id and node.has_method("apply_a3game_input"):
				node.call("apply_a3game_input", input_state)
		return {"ok": true, "operation": operation, "controller_id": controller_id, "entity_id": entity_id}
	if operation == "world.reset":
		var world_id := str(message.get("world_id", ""))
		if world_id.is_empty():
			world_id = DEFAULT_WORLD_ID
		var controllers_to_remove: Array = []
		var entities_to_remove: Array = []
		for controller_id in _sessions:
			var session: Dictionary = _sessions[controller_id]
			if str(session.get("world_id", DEFAULT_WORLD_ID)) == world_id:
				controllers_to_remove.append(controller_id)
				entities_to_remove.append(str(session.get("entity_id", "")))
		for controller_id in controllers_to_remove:
			var previous: Dictionary = _sessions.get(controller_id, {})
			_sessions.erase(controller_id)
			session_left.emit(previous)
		for entity_id in entities_to_remove:
			_last_inputs.erase(entity_id)
		world_reset.emit(world_id)
		return {
			"ok": true,
			"operation": operation,
			"world_id": world_id,
			"removed_sessions": controllers_to_remove.size(),
			"sessions": _sessions.size(),
		}
	if operation == "entity.clear":
		var entity_id := str(message.get("entity_id", ""))
		if entity_id.is_empty():
			return {"ok": false, "operation": operation, "error": "entity_id is required"}
		var destroy_value = message.get("destroy_actor", true)
		if typeof(destroy_value) != TYPE_BOOL:
			return {"ok": false, "operation": operation, "error": "destroy_actor must be a boolean"}
		var destroy_actor: bool = destroy_value
		var controllers_to_remove: Array = []
		for controller_id in _sessions:
			var session: Dictionary = _sessions[controller_id]
			if str(session.get("entity_id", "")) == entity_id:
				controllers_to_remove.append(controller_id)
		for controller_id in controllers_to_remove:
			var previous: Dictionary = _sessions.get(controller_id, {})
			_sessions.erase(controller_id)
			session_left.emit(previous)
		_last_inputs.erase(entity_id)
		var matched_nodes := 0
		var destroy_queued_nodes := 0
		for node in get_tree().get_nodes_in_group("a3game_runtime_entity"):
			if str(node.get("a3game_entity_id")) == entity_id:
				matched_nodes += 1
				if destroy_actor and node.has_method("clear_a3game_entity"):
					node.call("clear_a3game_entity")
					destroy_queued_nodes += 1
		return {
			"ok": true,
			"operation": operation,
			"entity_id": entity_id,
			"destroy_actor": destroy_actor,
			"removed_sessions": controllers_to_remove.size(),
			"matched_nodes": matched_nodes,
			"destroy_queued_nodes": destroy_queued_nodes,
			"sessions": _sessions.size(),
		}
	return {"ok": false, "operation": operation, "error": "Unsupported runtime operation"}


func sessions_snapshot(world_id: String = "") -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	for session in _sessions.values():
		if world_id.is_empty() or str(session.get("world_id", DEFAULT_WORLD_ID)) == world_id:
			result.append(session.duplicate(true))
	return result


func last_input_for(entity_id: String) -> Dictionary:
	return Dictionary(_last_inputs.get(entity_id, {})).duplicate(true)


func bind_entity(node: Node, entity_id: String) -> Dictionary:
	var normalized_id := entity_id.strip_edges()
	if node == null:
		return {"ok": false, "error": "node is required"}
	if normalized_id.is_empty():
		return {"ok": false, "error": "entity_id is required"}
	if not node.has_method("apply_a3game_input"):
		return {"ok": false, "error": "node must implement apply_a3game_input"}
	var has_identity_property := false
	for property in node.get_property_list():
		if str(property.get("name", "")) == "a3game_entity_id":
			has_identity_property = true
			break
	if not has_identity_property:
		return {"ok": false, "error": "node must expose a3game_entity_id"}
	node.set("a3game_entity_id", normalized_id)
	node.add_to_group("a3game_runtime_entity")
	return {"ok": true, "entity_id": normalized_id, "node_path": str(node.get_path())}
