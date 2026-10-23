# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure navigation math for Unreal-Engine-style viewport navigation.

All functions are side-effect free and only depend on ``mathutils`` so they
can be unit-tested inside Blender's Python without a window manager.

Conventions
-----------
* ``view_rotation`` is ``RegionView3D.view_rotation``: the rotation that
  transforms view-space into world-space.  The viewport camera looks down
  the view-space ``-Z`` axis with ``+Y`` up and ``+X`` right.
* Camera position (perspective): ``view_location - forward * view_distance``.
* ``dx`` / ``dy`` are mouse deltas in pixels, ``+x`` right, ``+y`` up
  (Blender event coordinates have their origin at the bottom-left corner).
"""

import math

from mathutils import Vector

# Guard rails
PITCH_LIMIT_DEG = 89.995     # free-look clamp, UE clamps at straight up/down
ORBIT_LIMIT_DEG = 89.995     # turntable elevation clamp
MIN_DISTANCE = 1e-5
MAX_DISTANCE = 1e7


def view_forward(view_rotation):
    """Direction the viewport camera is looking at (world space, normalized)."""
    return (view_rotation @ Vector((0.0, 0.0, -1.0))).normalized()


def view_right(view_rotation):
    """Camera right axis in world space."""
    return (view_rotation @ Vector((1.0, 0.0, 0.0))).normalized()


def view_up(view_rotation):
    """Camera up axis in world space."""
    return (view_rotation @ Vector((0.0, 1.0, 0.0))).normalized()


def camera_position(view_location, view_distance, view_rotation):
    """World-space position of the viewport camera (perspective)."""
    return view_location - view_forward(view_rotation) * view_distance


def forward_to_azimuth_elevation(forward):
    """Decompose a direction into turntable azimuth/elevation (radians).

    Azimuth is measured around world +Z (from +X toward +Y), elevation is
    the angle above the XY plane.
    """
    f = forward.normalized()
    elevation = math.asin(max(-1.0, min(1.0, f.z)))
    azimuth = math.atan2(f.y, f.x)
    return azimuth, elevation


def azimuth_elevation_to_forward(azimuth, elevation):
    """Inverse of :func:`forward_to_azimuth_elevation`."""
    ce = math.cos(elevation)
    return Vector((ce * math.cos(azimuth), ce * math.sin(azimuth), math.sin(elevation)))


def rotation_from_forward(forward):
    """Roll-free view rotation looking along ``forward`` (-Z onto forward)."""
    return forward.normalized().to_track_quat('-Z', 'Y')


def apply_free_look(view_rotation, dx_px, dy_px, yaw_sens_deg, pitch_sens_deg,
                    invert_y=False):
    """FPS-style look: yaw around world +Z, pitch around camera-local +X.

    Returns the new view rotation with the total elevation clamped to
    +/-PITCH_LIMIT_DEG (matching UE, which does not let you flip over while
    looking around with RMB held).
    """
    if dx_px == 0.0 and dy_px == 0.0:
        return view_rotation.copy()

    from mathutils import Euler

    yaw = math.radians(dx_px * yaw_sens_deg)
    # Mouse right (dx > 0) must look right  -> negative rotation around +Z.
    yaw_rot = Euler((0.0, 0.0, -yaw), 'XYZ').to_quaternion()

    pitch = math.radians(dy_px * (pitch_sens_deg * (-1.0 if invert_y else 1.0)))
    # Mouse up (dy > 0) must look up -> positive rotation around local +X.
    pitch_rot = Euler((pitch, 0.0, 0.0), 'XYZ').to_quaternion()

    rot = yaw_rot @ view_rotation @ pitch_rot

    # Clamp elevation so the camera cannot flip over.
    limit = math.radians(PITCH_LIMIT_DEG)
    _, elev = forward_to_azimuth_elevation(view_forward(rot))
    if abs(elev) > limit:
        excess = elev - max(-limit, min(limit, elev))
        fix = Euler((math.copysign(abs(excess), excess) * -1.0, 0.0, 0.0), 'XYZ').to_quaternion()
        rot = rot @ fix
    return rot


def apply_turntable(azimuth, elevation, dx_px, dy_px, orbit_sens_deg,
                    invert_x=False, invert_y=False):
    """UE Alt+LMB orbit step. Returns new (azimuth, elevation) in radians.

    The scene follows the mouse (Maya / Unreal Ed convention): dragging
    right spins the model to the right (camera orbits left, azimuth
    decreases); dragging down pulls the front of the world downward, so
    its top rotates toward you and the camera ends up above the pivot
    (forward elevation decreases).
    """
    d_az = math.radians(-dx_px * orbit_sens_deg * (-1.0 if invert_x else 1.0))
    d_el = math.radians(dy_px * orbit_sens_deg * (-1.0 if invert_y else 1.0))
    azimuth_new = azimuth + d_az
    limit = math.radians(ORBIT_LIMIT_DEG)
    elevation_new = max(-limit, min(limit, elevation + d_el))
    return azimuth_new, elevation_new


def dolly_distance(view_distance, dy_px, sensitivity=0.004):
    """UE Alt+RMB dolly: dragging up zooms in toward the pivot.

    Exponential scaling keeps the feel uniform at any distance.
    """
    factor = math.exp(-dy_px * sensitivity)
    return max(MIN_DISTANCE, min(MAX_DISTANCE, view_distance * factor))


def pan_delta_world(context, region, rv3d, from_coord, to_coord):
    """World-space delta that keeps ``from_coord`` under the cursor after the
    cursor moved to ``to_coord`` (grab/track-pan like UE Alt+MMB / MMB drag).

    Works in both perspective and orthographic (uses the region projection).
    ``*_coord`` are (x, y) region-relative pixel coordinates.
    """
    from bpy_extras import view3d_utils

    depth = rv3d.view_location.copy()
    p0 = view3d_utils.region_2d_to_location_3d(region, rv3d, from_coord, depth)
    p1 = view3d_utils.region_2d_to_location_3d(region, rv3d, to_coord, depth)
    return p1 - p0


def speed_for_level(level, base_speed, step=1.6):
    """Flight speed in units/second for a UE-style discrete speed level."""
    return base_speed * (step ** (level - 4))


def clamp_lens(lens_mm, lo=6.0, hi=500.0):
    return max(lo, min(hi, lens_mm))


def fly_displacement(forward, right, up, world_up, move_state, speed, dt):
    """Displacement vector for one flight tick.

    ``move_state`` is a dict of boolean flags: forward, backward, left,
    right, up_global, down_global, up_local, down_local.
    UE: W/S fly along the (pitch-including) camera forward, A/D strafe
    along camera right, E/Q ride along world +Z/-Z, R/F along camera up.
    """
    disp = Vector((0.0, 0.0, 0.0))
    if move_state.get('forward'):
        disp += forward
    if move_state.get('backward'):
        disp -= forward
    if move_state.get('right'):
        disp += right
    if move_state.get('left'):
        disp -= right
    if move_state.get('up_global'):
        disp += world_up
    if move_state.get('down_global'):
        disp -= world_up
    if move_state.get('up_local'):
        disp += up
    if move_state.get('down_local'):
        disp -= up
    if disp.length == 0.0:
        return disp
    return disp.normalized() * (speed * dt)
