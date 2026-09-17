'Local mesh rigging, skin weights and linear blend skinning functions.'
from .mesh import BodyFrame, CreatureMesh
from .types import RigResult, to_motion_template
from .generate import rig_skeleton
from .templates import LimbGroup, RIG_PRESETS, resolve_rig_preset
from .skin_generate import skin_mesh, bone_distances, distance_weights, prune_to_k, smooth_weights
from .skin_templates import SKIN_PRESETS, SKIN_PRESET_FOR_MORPHOLOGY
from .skin_units import SkinWeights, pose_matrices, deform, validate_weights
from .skin_checks import validate_skin, format_skin_report
