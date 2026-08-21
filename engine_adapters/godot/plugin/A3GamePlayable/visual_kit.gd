class_name A3GameVisualKit
extends RefCounted


static func pbr_material(
	color: Color,
	roughness: float = 0.45,
	metallic: float = 0.0,
	emission: Color = Color.TRANSPARENT,
) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = clampf(roughness, 0.0, 1.0)
	material.metallic = clampf(metallic, 0.0, 1.0)
	if emission.a > 0.0 or emission.r > 0.0 or emission.g > 0.0 or emission.b > 0.0:
		material.emission_enabled = true
		material.emission = emission
	return material


static func create_sun(
	color: Color = Color("fff3d5"),
	energy: float = 1.0,
	rotation_degrees: Vector3 = Vector3(-55.0, -30.0, 0.0),
) -> DirectionalLight3D:
	var light := DirectionalLight3D.new()
	light.light_color = color
	light.light_energy = maxf(0.0, energy)
	light.rotation_degrees = rotation_degrees
	light.shadow_enabled = true
	return light


static func create_fill(
	color: Color = Color("7e8dff"),
	energy: float = 2.0,
	radius: float = 12.0,
) -> OmniLight3D:
	var light := OmniLight3D.new()
	light.light_color = color
	light.light_energy = maxf(0.0, energy)
	light.omni_range = maxf(0.01, radius)
	return light
