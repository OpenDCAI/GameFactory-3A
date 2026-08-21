class_name A3GameInputState
extends RefCounted


const NUMERIC_FIELDS := ["move_x", "move_y", "yaw", "pitch", "timestamp"]


static func normalize(raw_input: Variant) -> Dictionary:
	if not raw_input is Dictionary:
		return {"ok": false, "error": "input state must be a Dictionary"}
	var raw: Dictionary = raw_input
	for field in NUMERIC_FIELDS:
		if raw.has(field) and typeof(raw[field]) not in [TYPE_INT, TYPE_FLOAT]:
			return {"ok": false, "error": "%s must be numeric" % field}
		if raw.has(field) and not is_finite(float(raw[field])):
			return {"ok": false, "error": "%s must be finite" % field}
	for field in ["run", "jump"]:
		if raw.has(field) and typeof(raw[field]) != TYPE_BOOL:
			return {"ok": false, "error": "%s must be a boolean" % field}
	if raw.has("seq"):
		if typeof(raw["seq"]) not in [TYPE_INT, TYPE_FLOAT]:
			return {"ok": false, "error": "seq must be an integer"}
		var sequence := float(raw["seq"])
		if not is_finite(sequence) or not is_equal_approx(sequence, round(sequence)):
			return {"ok": false, "error": "seq must be an integer"}
	return {
		"ok": true,
		"input": {
			"move_x": clampf(float(raw.get("move_x", 0.0)), -1.0, 1.0),
			"move_y": clampf(float(raw.get("move_y", 0.0)), -1.0, 1.0),
			"run": raw.get("run", false),
			"jump": raw.get("jump", false),
			"yaw": float(raw.get("yaw", 0.0)),
			"pitch": clampf(float(raw.get("pitch", 0.0)), -PI * 0.5, PI * 0.5),
			"seq": int(raw.get("seq", 0)),
			"timestamp": float(raw.get("timestamp", 0.0)),
		},
	}
