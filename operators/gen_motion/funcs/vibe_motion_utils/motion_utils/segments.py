"""Execute a segment's action intents to produce a standalone motion clip."""
from __future__ import annotations
from .skeleton_templates import MotionClip
from .primitives import apply_primitive
from .skeleton_templates import SkeletonPlan

def realize_segment(segment, plan: SkeletonPlan, *, fps: float=30.0) -> MotionClip:
    """Expand one :class:`~intent.MotionSegment` into a standalone clip."""
    clip = MotionClip.rest_clip(plan.template, segment.num_frames, fps=fps)
    clip.log.append(f'=== segment {segment.name} ({segment.num_frames} frames) ===')
    for intent in segment.intents:
        apply_primitive(clip, plan, intent.primitive, intent.role, dict(intent.params))
    return clip
