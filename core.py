# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared state for the Unreal-Engine-style navigation add-on.

Keeps the orbit pivot (UE's "point of interest"), the flight speed level
and a registry of running modal operators.  The registry doubles as the
hook the test-suite uses to drive modal operators with synthetic events.
"""

import os
import json

import bpy
from mathutils import Vector

# Instances of currently running modal operators, by operator key.
# e.g. {"fly": <Operator>, "orbit": <Operator>}
ACTIVE = {}

# UE-style orbit pivot: persists while Blender runs (like UE's focus point).
PIVOT = {
    "location": Vector((0.0, 0.0, 0.0)),
    "valid": False,
}

# Flight speed level (1..15), survives between modal sessions.
_SPEED_LEVEL = None

# Keymap items disabled from the user keyconfig, plus the context-menu
# operators we replay on a clean RMB click.  Populated by keymaps.install().
DISABLED_DEFAULT_ITEMS = []   # list of dicts (see keymaps.py)
MENU_REPLAY = []              # list of dicts (see keymaps.py)

# Mode identifier -> user-keyconfig keymap name (for menu replay lookup).
MODE_TO_KEYMAP = {
    'OBJECT': 'Object Mode',
    'EDIT_MESH': 'Mesh',
    'EDIT_CURVE': 'Curve',
    'EDIT_CURVES': 'Curves',
    'EDIT_SURFACE': 'Curve',
    'EDIT_METABALL': 'Metaball',
    'EDIT_ARMATURE': 'Armature',
    'EDIT_LATTICE': 'Lattice',
    'POSE': 'Pose',
    'SCULPT': 'Sculpt',
    'PAINT_WEIGHT': 'Weight Paint',
    'PAINT_VERTEX': 'Vertex Paint',
    'PAINT_TEXTURE': 'Image Paint',
    'PARTICLE': 'Particle',
    'OBJECT_MODE_MESH': 'Mesh',
}


def get_prefs(context=None):
    ctx = context or bpy.context
    try:
        return ctx.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def get_speed_level():
    global _SPEED_LEVEL
    if _SPEED_LEVEL is None:
        prefs = get_prefs()
        default = 4
        if prefs is not None:
            default = int(prefs.fly_default_level)
        _SPEED_LEVEL = max(1, min(15, default))
    return _SPEED_LEVEL


def set_speed_level(level):
    global _SPEED_LEVEL
    _SPEED_LEVEL = max(1, min(15, int(level)))
    return _SPEED_LEVEL


def get_pivot(context):
    """Return the orbit pivot, adopting the current view center if the user
    has not focused on anything yet (same behavior as UE)."""
    if not PIVOT["valid"]:
        rv3d = getattr(context, "region_data", None)
        if rv3d is not None and hasattr(rv3d, "view_location"):
            PIVOT["location"] = rv3d.view_location.copy()
            PIVOT["valid"] = True
        else:
            PIVOT["location"] = Vector((0.0, 0.0, 0.0))
            PIVOT["valid"] = True
    return PIVOT["location"]


def set_pivot(location):
    PIVOT["location"] = location.copy()
    PIVOT["valid"] = True


# ---------------------------------------------------------------------------
# HUD overlay (flight speed + orbit pivot marker), UE-style.
# ---------------------------------------------------------------------------

_handle = None


def _draw_hud():
    if not ACTIVE:
        return
    prefs = get_prefs()
    if prefs is None or not prefs.show_hud:
        return
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    import bpy as _bpy
    from bpy_extras import view3d_utils

    context = _bpy.context
    region = context.region
    if region is None or getattr(context, "space_data", None) is None:
        return
    if context.space_data.type != 'VIEW_3D':
        return

    font = 0
    size = 12
    blf.size(font, size)
    x, y = 14, region.height - 60

    import ue_navigation.navmath as navmath

    if "fly" in ACTIVE:
        level = get_speed_level()
        prefs2 = get_prefs()
        speed = navmath.speed_for_level(level, prefs2.fly_base_speed)
        text = "UE Fly  |  Speed %d  (%.1f m/s)  |  W/S/A/D Q/E R/F  ·  Wheel = speed" % (level, speed)
        blf.position(font, x, y, 0)
        blf.color(font, 1.0, 0.85, 0.3, 0.95)
        blf.draw(font, text)
        y -= 16
    if "orbit" in ACTIVE or "dolly" in ACTIVE:
        rv3d = context.region_data
        if rv3d is not None:
            co = view3d_utils.location_3d_to_region_2d(region, rv3d, PIVOT["location"])
            if co is not None:
                shader = gpu.shader.from_builtin('UNIFORM_COLOR')
                gpu.state.blend_set('ALPHA')
                r = 7.0
                verts_lines = [((co[0] - r, co[1]), (co[0] + r, co[1])),
                               ((co[0], co[1] - r), (co[0], co[1] + r))]
                batch = batch_for_shader(shader, 'LINES', {"pos": [v for pair in verts_lines for v in pair]})
                shader.uniform_float("color", (1.0, 0.7, 0.1, 0.9))
                batch.draw(shader)
                gpu.state.blend_set('NONE')
    blf.size(font, 11)


def hud_register():
    global _handle
    if _handle is None:
        try:
            _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_hud, (), 'WINDOW', 'POST_PIXEL')
        except Exception:
            _handle = None


def hud_unregister():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception:
            pass
        _handle = None


# ---------------------------------------------------------------------------
# Crash-safe backup of keymap changes (restored if Blender died while the
# add-on had disabled default items).
# ---------------------------------------------------------------------------

def _backup_path():
    try:
        config_dir = bpy.utils.user_resource('CONFIG', create=True)
    except Exception:
        return None
    d = os.path.join(config_dir, "ue_navigation")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "keymap_backup.json")
    except Exception:
        return None


def backup_save(payload):
    path = _backup_path()
    if path is None:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def backup_load():
    path = _backup_path()
    if path is None or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def backup_delete():
    path = _backup_path()
    if path is not None and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
