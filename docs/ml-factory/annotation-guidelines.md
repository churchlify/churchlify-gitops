# Ball Annotation Guidelines

Create one bounding box per visible soccer ball and use only the `ball` label.
Make the box tight around the visible ball, including a partially occluded ball
only when its identity is clear. Annotate balls that are blurred, in flight,
near players, near field boundaries, or distant. Annotate multiple balls
separately.

Do not annotate logos, feet, heads, bags, field markings, light reflections,
or other circular objects. For difficult lighting, use the visible evidence and
do not enlarge a box to include uncertain pixels. When a ball is too ambiguous
to distinguish from a false visual object, leave it unannotated and record the
frame for review. Do not add player, pose, jersey, team, tracking, segmentation,
or field labels.
