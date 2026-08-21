class_name A3GameHudLayer
extends CanvasLayer


var _title_label: Label
var _status_label: Label


func _ready() -> void:
	_title_label = Label.new()
	_title_label.position = Vector2(24.0, 18.0)
	_title_label.add_theme_font_size_override("font_size", 22)
	_title_label.add_theme_color_override("font_color", Color("56e8ff"))
	add_child(_title_label)
	_status_label = Label.new()
	_status_label.position = Vector2(24.0, 50.0)
	_status_label.add_theme_font_size_override("font_size", 16)
	_status_label.add_theme_color_override("font_color", Color.WHITE)
	add_child(_status_label)


func set_title(value: String) -> void:
	if _title_label != null:
		_title_label.text = value


func set_status(values: Dictionary) -> void:
	if _status_label == null:
		return
	var keys := values.keys()
	keys.sort()
	var fields: PackedStringArray = []
	for key in keys:
		fields.append("%s: %s" % [str(key), str(values[key])])
	_status_label.text = "  •  ".join(fields)
