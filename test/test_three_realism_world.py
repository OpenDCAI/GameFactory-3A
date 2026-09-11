import unittest

from engine_adapters.three_js.world._internal.specs import EnvironmentSpec, WorldSpec


class TestRealismWorld(unittest.TestCase):
    def test_round_trip_water_and_sky(self):
        data = {
            "show_sky": False,
            "environment_rotation_degrees": 90,
            "water": [{"water_id": "lake", "size": [20, 30],
                       "position": [2, 1, -3], "normal_artifact_id": "water-normal",
                       "options": {"quality": "standard", "waveHeight": 0.08}}],
        }
        first = EnvironmentSpec.from_dict(data)
        second = EnvironmentSpec.from_dict(first.to_dict())
        self.assertEqual(first, second)
        self.assertEqual(second.background_rotation_degrees, 90)
        self.assertEqual(second.water[0]["position"], {"x": 2, "y": 1, "z": -3})
        world = WorldSpec.from_dict({"world_id": "scene", "environment": data})
        self.assertIn("water-normal", world.artifact_ids())

    def test_wind_round_trip_and_validation(self):
        env = EnvironmentSpec.from_dict({"wind": {"velocity": [3, 0, -1], "gustStrength": 0.5, "seed": 3}})
        self.assertEqual(EnvironmentSpec.from_dict(env.to_dict()), env)
        self.assertEqual(env.wind["velocity"], {"x": 3, "y": 0, "z": -1})
        for wind in ({"velocity": [1, 2]}, {"velocity": [0, float("inf"), 0]},
                     {"gustStrength": -1}, {"gustPeriod": 0}, {"seed": float("nan")}):
            with self.subTest(wind=wind), self.assertRaises(ValueError):
                EnvironmentSpec.from_dict({"wind": wind})

    def test_no_water_preserves_legacy_world(self):
        self.assertEqual(EnvironmentSpec.from_dict({}).water, ())

    def test_rejects_invalid_water(self):
        cases = [
            [{"water_id": "bad id"}],
            [{"water_id": "lake", "size": -1}],
            [{"water_id": "lake", "size": [20]}],
            [{"water_id": "lake", "size": float("inf")}],
            [{"water_id": "lake", "position": [0, float("nan"), 0]}],
            [{"water_id": "lake", "options": {"quality": "ultra"}}],
            [{"water_id": "lake"}, {"water_id": "lake"}],
            {},
        ]
        for water in cases:
            with self.subTest(water=water), self.assertRaises(ValueError):
                EnvironmentSpec.from_dict({"water": water})


if __name__ == "__main__":
    unittest.main()
