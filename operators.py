# SPDX-License-Identifier: GPL-3.0-or-later
"""Modal operators implementing Unreal Engine viewport navigation.

Control scheme (from the official UE "Viewport Controls" documentation):

Perspective, RMB held (fly / look):
    RMB hold + mouse  ......... look around (free-look, clamped at up/down)
    RMB + W/S/A/D ............. fly forward/back/left/right
    RMB + Up/Down/Left/Right .. same as WASD
    RMB + Numpad 8/2/4/6 ...... same as WASD
    RMB + E / Q ............... move up / down in *global* space
    RMB + PageUp / PageDown ... global up / down
    RMB + Numpad 9 / 7 ........ global up / down
    RMB + R / F ............... move up / down in *local* (camera) space
    RMB + Z / C ............... raise / lower field of view
    RMB + Numpad 1 / 3 ........ raise / lower field of view
    RMB + scroll .............. increase / decrease fly speed
    LMB while RMB held ........ pan up/down/left/right (LMB+RMB drag)

Orthographic, RMB held: drag pans the camera (UE ortho behavior).

Pivot navigation:
    F .......................... focus on selection, sets orbit pivot
    Alt + LMB drag .............. orbit around pivot
    Alt + RMB drag .............. dolly (zoom) toward/away from pivot
    Alt + MMB drag .............. track (pan) in direction of mouse

Plain mouse:
    MMB drag ................... pan (UE); Blender's default MMB orbit is
                                 disabled by the add-on (re-enabled on
                                 uninstall)
    Scroll ..................... dolly forward/backward (Blender default
                                 view3d.zoom, identical to UE)
    LMB drag (perspective) ..... UE crash-cam (optional, off by default:
                                 Blender 5 reserves LMB-drag for its
                                 select/move tools)

Each operator's modal logic lives in module-level ``*_modal`` functions so
the test-suite can drive them with synthetic events without a real event
loop; ``*_tick`` functions advance continuous (timer-driven) motion with an
explicit dt.
"""

import math
import os
import time

import bpy
import mathutils
from mathutils import Vector

if False:  # pragma: no cover - typing only
    from bpy_types import Operator

from . import core, navmath


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _prefs():
    return core.get_prefs() or _DefaultPrefs()


class _DefaultPrefs:
    look_sens = 0.10
    orbit_sens = 0.25
    dolly_sens = 0.004
    fly_base_speed = 12.0
    fov_rate = 40.0
    invert_look_y = False
    invert_orbit_x = False
    invert_orbit_y = False


def _tag_redraw(context):
    area = getattr(context, "area", None)
    if area is not None:
        try:
            area.tag_redraw()
        except ReferenceError:
            pass


def _region_coords(event):
    return (float(getattr(event, "mouse_region_x", 0.0)),
            float(getattr(event, "mouse_region_y", 0.0)))


def _auto_perspective_enabled():
    """Mirror Blender's Navigation > Auto-Perspective preference.

    When enabled (the default), orbiting out of an axis-aligned
    orthographic view (Top/Front/Side, e.g. after an Alt+MMB axis snap)
    switches back to perspective - and aligning to an axis switches to
    orthographic.  The add-on replaces MMB orbit with MMB pan, so it must
    perform this ORTHO -> PERSP step itself, otherwise a second MMB drag
    from Top view would only pan and never return to perspective.
    """
    try:
        inputs = getattr(bpy.context.preferences, "inputs", None)
        if inputs is not None and hasattr(inputs, "view_rotate_auto_perspective"):
            return bool(inputs.view_rotate_auto_perspective)
    except Exception:
        pass
    return True


def _restore_perspective_if_ortho(rv3d):
    """Switch an ORTHO RegionView3D back to PERSP if Auto-Perspective is on.

    Only touches axis views ('ORTHO'); camera views ('CAMERA') are left
    alone.  Returns True when a switch happened.
    """
    try:
        if rv3d is None:
            return False
        if getattr(rv3d, "view_perspective", 'PERSP') != 'ORTHO':
            return False
        if not _auto_perspective_enabled():
            return False
        rv3d.view_perspective = 'PERSP'
        return True
    except Exception:
        return False


def _end(op, context, key):
    if getattr(op, "_timer", None) is not None:
        try:
            context.window_manager.event_timer_remove(op._timer)
        except Exception:
            pass
        op._timer = None
    core.ACTIVE.pop(key, None)
    area = getattr(context, "area", None)
    if area is not None:
        try:
            area.header_text_set(None)
        except ReferenceError:
            pass
    _tag_redraw(context)


# ---------------------------------------------------------------------------
# 1) Fly / Look  (RMB held)
# ---------------------------------------------------------------------------

class UE_NAV_OT_fly(bpy.types.Operator):
    bl_idname = "view3d.unreal_fly"
    bl_label = "Unreal Fly / Look"
    bl_description = ("Hold Right Mouse to look around and fly with W/S/A/D "
                      "(Q/E global up/down, R/F local up/down, Z/C field of "
                      "view, wheel = speed) - Unreal Engine style")

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region", None) is not None
                and context.region.type == 'WINDOW')

    def invoke(self, context, event):
        self._area = context.area
        self._region = context.region
        self._rv3d = context.region_data
        self._space = context.space_data
        self._window = context.window

        self._skip_first = True
        self._last_xy = _region_coords(event)
        self._start_xy = self._last_xy
        self._press_time = time.monotonic()
        self._moved = 0.0
        self._nav_key_used = False
        self._left_held = False
        self._ortho = not self._rv3d.is_perspective
        self._flags = {}
        self._last_tick = time.monotonic()
        self._timer = context.window_manager.event_timer_add(1.0 / 120.0, window=context.window)

        core.ACTIVE["fly"] = self
        self._speed_level = core.get_speed_level()
        try:
            context.area.header_text_set("UE Fly | WASD·QE·RF · Wheel=speed · release RMB to exit")
        except Exception:
            pass
        context.window_manager.modal_handler_add(self)
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return fly_modal(self, context, event)


def fly_set_speed(context, delta):
    level = core.set_speed_level(core.get_speed_level() + delta)
    area = getattr(context, "area", None)
    if area is not None:
        try:
            area.header_text_set("UE Fly Speed: %d" % level)
        except Exception:
            pass
    return level


_KEY_TO_FLAG = {
    'W': 'forward', 'UP_ARROW': 'forward', 'NUMPAD_8': 'forward',
    'S': 'backward', 'DOWN_ARROW': 'backward', 'NUMPAD_2': 'backward',
    'A': 'left', 'LEFT_ARROW': 'left', 'NUMPAD_4': 'left',
    'D': 'right', 'RIGHT_ARROW': 'right', 'NUMPAD_6': 'right',
    'E': 'up_global', 'PAGE_UP': 'up_global', 'NUMPAD_9': 'up_global',
    'Q': 'down_global', 'PAGE_DOWN': 'down_global', 'NUMPAD_7': 'down_global',
    'R': 'up_local',
    'Z': 'fov_up', 'NUMPAD_1': 'fov_up',
    'C': 'fov_down', 'NUMPAD_3': 'fov_down',
}


def fly_modal(op, context, event):
    etype = event.type
    evalue = event.value

    if etype == 'RIGHTMOUSE' and evalue == 'RELEASE':
        # A "clean click" (press + release without really moving or using a
        # nav key) replays the context menu.  The threshold must tolerate
        # natural hand jitter - the cursor is compared END-TO-END
        # (press point vs release point), not the accumulated path.
        drift = math.hypot(op._last_xy[0] - op._start_xy[0],
                           op._last_xy[1] - op._start_xy[1])
        clean_click = (drift <= 12.0 and not op._nav_key_used)
        _end(op, context, "fly")
        if clean_click:
            replay_context_menu(context)
        return {'FINISHED'}

    if etype == 'ESC':
        _end(op, context, "fly")
        return {'FINISHED'}

    if etype == 'WINDOW_DEACTIVATE':
        _end(op, context, "fly")
        return {'FINISHED'}

    if etype == 'MOUSEMOVE' or etype == 'INBETWEEN_MOUSEMOVE':
        xy = _region_coords(event)
        if op._skip_first:
            op._skip_first = False
            op._last_xy = xy
            return {'RUNNING_MODAL'}
        dx = xy[0] - op._last_xy[0]
        dy = xy[1] - op._last_xy[1]
        op._moved += abs(dx) + abs(dy)
        op._last_xy = xy
        rv3d = op._rv3d
        if op._ortho or op._left_held:
            # UE ortho RMB-drag pans; UE LMB+RMB drag pans in perspective.
            delta = navmath.pan_delta_world(context, op._region, rv3d,
                                            (xy[0] - dx, xy[1] - dy), xy)
            rv3d.view_location -= delta
        else:
            p = _prefs()
            cam_pos = navmath.camera_position(rv3d.view_location, rv3d.view_distance, rv3d.view_rotation)
            rot = navmath.apply_free_look(rv3d.view_rotation, dx, dy,
                                          p.look_sens, p.look_sens, p.invert_look_y)
            rv3d.view_rotation = rot
            rv3d.view_location = cam_pos + navmath.view_forward(rot) * rv3d.view_distance
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    if etype == 'LEFTMOUSE':
        if evalue == 'PRESS':
            op._left_held = True
            op._nav_key_used = True
        elif evalue == 'RELEASE':
            op._left_held = False
        return {'RUNNING_MODAL'}

    if etype == 'WHEELUPMOUSE':
        fly_set_speed(context, +1)
        return {'RUNNING_MODAL'}
    if etype == 'WHEELDOWNMOUSE':
        fly_set_speed(context, -1)
        return {'RUNNING_MODAL'}

    if etype == 'F' and evalue == 'PRESS':
        # UE-style: F focuses the selection at any time - including while
        # flying.  Focus is applied instantly (no smooth-view animation)
        # so it cannot fight the ongoing flight motion.
        _instant_focus(context, op._rv3d)
        return {'RUNNING_MODAL'}

    if etype in _KEY_TO_FLAG:
        flag = _KEY_TO_FLAG[etype]
        if evalue == 'PRESS' or evalue == 'RELEASE':
            op._flags[flag] = (evalue == 'PRESS')
            op._nav_key_used = True
            return {'RUNNING_MODAL'}

    if etype == 'TIMER':
        if getattr(event, "timer", None) is not None and op._timer is not None \
                and event.timer != op._timer:
            return {'PASS_THROUGH'}
        dt = float(getattr(event, "time_delta", 0.0) or 0.0)
        if dt <= 0.0:
            now = time.monotonic()
            dt = now - op._last_tick
            op._last_tick = now
        fly_tick(op, context, dt)
        return {'RUNNING_MODAL'}

    # Swallow everything else while flying (gizmos, other hotkeys...) so the
    # session behaves like UE where RMB grabs the viewport.
    if evalue in ('PRESS', 'RELEASE', 'CLICK', 'ANY'):
        return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}


def fly_tick(op, context, dt):
    """Advance continuous flight/FOV motion. ``dt`` in seconds."""
    dt = max(0.0, min(dt, 0.1))
    rv3d = op._rv3d
    if rv3d is None or not op._flags:
        return
    p = _prefs()
    speed = navmath.speed_for_level(core.get_speed_level(), p.fly_base_speed)
    rot = rv3d.view_rotation
    disp = navmath.fly_displacement(
        navmath.view_forward(rot), navmath.view_right(rot), navmath.view_up(rot),
        Vector((0.0, 0.0, 1.0)), op._flags, speed, dt)
    if disp.length > 0.0:
        if op._ortho:
            # UE: WASD flying is perspective-only; in ortho map it to pan.
            flat = Vector((disp.x, disp.y, 0.0))
            rv3d.view_location += flat
        else:
            rv3d.view_location += disp
        _tag_redraw(context)
    if op._space is not None and getattr(rv3d, "is_perspective", True):
        lens = op._space.lens
        new_lens = lens
        if op._flags.get('fov_up'):
            new_lens += p.fov_rate * dt
        if op._flags.get('fov_down'):
            new_lens -= p.fov_rate * dt
        new_lens = navmath.clamp_lens(new_lens)
        if abs(new_lens - lens) > 1e-9:
            op._space.lens = new_lens
            _tag_redraw(context)


def replay_context_menu(context):
    """Open the original right-click menu for the current region (the add-on
    disables the RMB-press menu handlers so RMB can be UE's fly modifier).

    Blender 5.x routes the viewport context menu through
    ``screen.region_context_menu`` (window-level keymap), which builds the
    correct menu for the region it is invoked on.  Popups cannot be spawned
    from inside a modal operator's event cycle, so the call is deferred to
    the next timer tick with the region context captured now."""
    window = getattr(context, "window", None)
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    space = getattr(context, "space_data", None)
    mode = getattr(context, "mode", 'OBJECT')

    def _open_menu():
        ctx = bpy.context
        # Primary: the captured mode-keymap menu (wm.call_menu works when
        # deferred to a timer; verified to spawn the popup window).
        km_name = core.MODE_TO_KEYMAP.get(mode, 'Object Mode')
        entry = None
        for e in core.MENU_REPLAY:
            if e["keymap"] == km_name:
                entry = e
                break
        if entry is None:
            for e in core.MENU_REPLAY:
                if e["keymap"] == 'Object Mode':
                    entry = e
                    break
        if entry is not None:
            try:
                op_call = getattr(bpy.ops, entry["idname"].partition(".")[0])
                op_call = getattr(op_call, entry["idname"].partition(".")[2])
                if window is not None and area is not None:
                    with ctx.temp_override(window=window, area=area,
                                           region=region, space_data=space):
                        op_call('INVOKE_DEFAULT', **entry["props"])
                else:
                    op_call('INVOKE_DEFAULT', **entry["props"])
                return
            except Exception:
                import traceback
                traceback.print_exc()
        # Fallback: generic region context menu.
        try:
            if window is not None and area is not None:
                with ctx.temp_override(window=window, area=area,
                                       region=region, space_data=space):
                    bpy.ops.screen.region_context_menu('INVOKE_DEFAULT')
            else:
                bpy.ops.screen.region_context_menu('INVOKE_DEFAULT')
        except Exception:
            import traceback
            traceback.print_exc()
    try:
        bpy.app.timers.register(_open_menu, first_interval=0.0, single_shot=True)  # noqa
    except TypeError:
        bpy.app.timers.register(_open_menu, first_interval=0.0)


# ---------------------------------------------------------------------------
# 2) Orbit (Alt + LMB drag)
# ---------------------------------------------------------------------------

def _update_pivot_from_selection(context):
    """While anything is selected, orbit/dolly pivot follows the selection
    center (Unreal: rotating spins around the selected actor).  With no
    selection the previous pivot (F / view center) is kept."""
    center = _selection_center(context)
    if center is not None:
        core.set_pivot(center)


class UE_NAV_OT_orbit(bpy.types.Operator):
    bl_idname = "view3d.unreal_orbit"
    bl_label = "Unreal Orbit"
    bl_description = "Alt + Left Mouse drag: orbit the camera around the pivot (last focused point)"

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region", None) is not None
                and context.region.type == 'WINDOW')

    def invoke(self, context, event):
        self._rv3d = context.region_data
        self._region = context.region
        self._skip_first = True
        self._last_xy = _region_coords(event)
        _update_pivot_from_selection(context)
        self._pivot = core.get_pivot(context).copy()
        self._distance = max(navmath.MIN_DISTANCE, self._rv3d.view_distance)
        f = navmath.view_forward(self._rv3d.view_rotation)
        self._az, self._el = navmath.forward_to_azimuth_elevation(f)
        self._snapshot = (self._rv3d.view_rotation.copy(),
                          self._rv3d.view_location.copy(),
                          self._rv3d.view_distance,
                          getattr(self._rv3d, "view_perspective", 'PERSP'))
        core.ACTIVE["orbit"] = self
        context.window_manager.modal_handler_add(self)
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return orbit_modal(self, context, event)


def orbit_modal(op, context, event):
    etype = event.type
    evalue = event.value

    if etype == 'LEFTMOUSE' and evalue == 'RELEASE':
        core.ACTIVE.pop("orbit", None)
        _tag_redraw(context)
        return {'FINISHED'}
    if etype == 'ESC':
        rot, loc, dist = op._snapshot[:3]
        op._rv3d.view_rotation = rot
        op._rv3d.view_location = loc
        op._rv3d.view_distance = dist
        if len(op._snapshot) > 3:
            try:
                op._rv3d.view_perspective = op._snapshot[3]
            except Exception:
                pass
        core.ACTIVE.pop("orbit", None)
        _tag_redraw(context)
        return {'CANCELLED'}
    if etype == 'WINDOW_DEACTIVATE':
        core.ACTIVE.pop("orbit", None)
        _tag_redraw(context)
        return {'FINISHED'}

    if etype in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        xy = _region_coords(event)
        if op._skip_first:
            op._skip_first = False
            op._last_xy = xy
            return {'RUNNING_MODAL'}
        dx = xy[0] - op._last_xy[0]
        dy = xy[1] - op._last_xy[1]
        op._last_xy = xy
        # Blender Auto-Perspective: orbiting from an ORTHO axis view
        # (e.g. Top after an Alt+MMB snap) returns to perspective.
        _restore_perspective_if_ortho(op._rv3d)
        p = _prefs()
        op._az, op._el = navmath.apply_turntable(
            op._az, op._el, dx, dy, p.orbit_sens,
            p.invert_orbit_x, p.invert_orbit_y)
        fwd = navmath.azimuth_elevation_to_forward(op._az, op._el)
        op._rv3d.view_rotation = navmath.rotation_from_forward(fwd)
        op._rv3d.view_location = op._pivot
        op._rv3d.view_distance = op._distance
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    if evalue in ('PRESS', 'RELEASE', 'CLICK', 'ANY'):
        return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# 3) Dolly toward pivot (Alt + RMB drag)
# ---------------------------------------------------------------------------

class UE_NAV_OT_dolly(bpy.types.Operator):
    bl_idname = "view3d.unreal_dolly"
    bl_label = "Unreal Dolly (Zoom)"
    bl_description = "Alt + Right Mouse drag: dolly (zoom) toward/away from the pivot"

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region", None) is not None
                and context.region.type == 'WINDOW')

    def invoke(self, context, event):
        self._rv3d = context.region_data
        self._skip_first = True
        self._last_xy = _region_coords(event)
        _update_pivot_from_selection(context)
        self._pivot = core.get_pivot(context).copy()
        self._distance = max(navmath.MIN_DISTANCE, self._rv3d.view_distance)
        self._snapshot = (self._rv3d.view_rotation.copy(),
                          self._rv3d.view_location.copy(),
                          self._rv3d.view_distance,
                          getattr(self._rv3d, "view_perspective", 'PERSP'))
        core.ACTIVE["dolly"] = self
        context.window_manager.modal_handler_add(self)
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return dolly_modal(self, context, event)


def dolly_modal(op, context, event):
    etype = event.type
    evalue = event.value

    if etype == 'RIGHTMOUSE' and evalue == 'RELEASE':
        core.ACTIVE.pop("dolly", None)
        _tag_redraw(context)
        return {'FINISHED'}
    if etype == 'ESC':
        rot, loc, dist = op._snapshot[:3]
        op._rv3d.view_rotation = rot
        op._rv3d.view_location = loc
        op._rv3d.view_distance = dist
        if len(op._snapshot) > 3:
            try:
                op._rv3d.view_perspective = op._snapshot[3]
            except Exception:
                pass
        core.ACTIVE.pop("dolly", None)
        _tag_redraw(context)
        return {'CANCELLED'}
    if etype == 'WINDOW_DEACTIVATE':
        core.ACTIVE.pop("dolly", None)
        _tag_redraw(context)
        return {'FINISHED'}

    if etype in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        xy = _region_coords(event)
        if op._skip_first:
            op._skip_first = False
            op._last_xy = xy
            return {'RUNNING_MODAL'}
        dx = xy[0] - op._last_xy[0]
        dy = xy[1] - op._last_xy[1]
        op._last_xy = xy
        # Blender convention: dollying out of an ORTHO view returns to
        # perspective (matches native view3d.dolly behavior).
        _restore_perspective_if_ortho(op._rv3d)
        p = _prefs()
        op._distance = navmath.dolly_distance(op._distance, dy, p.dolly_sens)
        op._rv3d.view_distance = op._distance
        op._rv3d.view_location = op._pivot
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    if evalue in ('PRESS', 'RELEASE', 'CLICK', 'ANY'):
        return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# 4) Track / Pan (Alt + MMB drag, and plain MMB drag)
# ---------------------------------------------------------------------------

class UE_NAV_OT_pan(bpy.types.Operator):
    bl_idname = "view3d.unreal_pan"
    bl_label = "Unreal Track (Pan)"
    bl_description = "Middle Mouse drag (or Alt + MMB): track the camera up/down/left/right"

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region", None) is not None
                and context.region.type == 'WINDOW')

    def invoke(self, context, event):
        self._rv3d = context.region_data
        self._region = context.region
        self._skip_first = True
        self._last_xy = _region_coords(event)
        core.ACTIVE["pan"] = self
        context.window_manager.modal_handler_add(self)
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return pan_modal(self, context, event)


def pan_modal(op, context, event):
    etype = event.type
    evalue = event.value

    if etype == 'MIDDLEMOUSE' and evalue == 'RELEASE':
        core.ACTIVE.pop("pan", None)
        _tag_redraw(context)
        return {'FINISHED'}
    if etype in ('ESC', 'WINDOW_DEACTIVATE'):
        core.ACTIVE.pop("pan", None)
        _tag_redraw(context)
        return ('CANCELLED' if etype == 'ESC' else 'FINISHED')

    if etype in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        xy = _region_coords(event)
        if op._skip_first:
            op._skip_first = False
            op._last_xy = xy
            return {'RUNNING_MODAL'}
        # BUGFIX: an MMB drag started from an axis-aligned ORTHO view
        # (Top/Bottom/Front/Back/Left/Right after an Alt+MMB axis snap)
        # must return to perspective instead of just panning inside ORTHO.
        # This mirrors Blender's Auto-Perspective (orbit/dolly switch
        # ORTHO -> PERSP) which the add-on's MMB-pan takeover would
        # otherwise suppress.
        _restore_perspective_if_ortho(op._rv3d)
        delta = navmath.pan_delta_world(context, op._region, op._rv3d,
                                        op._last_xy, xy)
        op._rv3d.view_location -= delta
        op._last_xy = xy
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    if evalue in ('PRESS', 'RELEASE', 'CLICK', 'ANY'):
        return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# 5) Crash-cam (perspective LMB drag, optional)
# ---------------------------------------------------------------------------

class UE_NAV_OT_crashcam(bpy.types.Operator):
    bl_idname = "view3d.unreal_crashcam"
    bl_label = "Unreal Crash Cam"
    bl_description = ("Left Mouse drag in perspective views: move forward/backward "
                      "and rotate left/right (UE perspective LMB behavior)")

    @classmethod
    def poll(cls, context):
        ok = (getattr(context, "space_data", None) is not None
              and context.space_data.type == 'VIEW_3D'
              and getattr(context, "region", None) is not None
              and context.region.type == 'WINDOW')
        if not ok:
            return False
        rv3d = getattr(context, "region_data", None)
        return rv3d is not None and rv3d.is_perspective

    def invoke(self, context, event):
        self._rv3d = context.region_data
        self._skip_first = True
        self._last_xy = _region_coords(event)
        core.ACTIVE["crashcam"] = self
        context.window_manager.modal_handler_add(self)
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return crashcam_modal(self, context, event)


def crashcam_modal(op, context, event):
    etype = event.type
    evalue = event.value

    if etype == 'LEFTMOUSE' and evalue == 'RELEASE':
        core.ACTIVE.pop("crashcam", None)
        _tag_redraw(context)
        return {'FINISHED'}
    if etype in ('ESC', 'WINDOW_DEACTIVATE'):
        core.ACTIVE.pop("crashcam", None)
        _tag_redraw(context)
        return ('CANCELLED' if etype == 'ESC' else 'FINISHED')

    if etype in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        xy = _region_coords(event)
        if op._skip_first:
            op._skip_first = False
            op._last_xy = xy
            return {'RUNNING_MODAL'}
        dx = xy[0] - op._last_xy[0]
        dy = xy[1] - op._last_xy[1]
        op._last_xy = xy
        rv3d = op._rv3d
        p = _prefs()
        # Horizontal mouse rotates left/right (like free-look yaw).
        cam_pos = navmath.camera_position(rv3d.view_location, rv3d.view_distance, rv3d.view_rotation)
        rot = navmath.apply_free_look(rv3d.view_rotation, dx, 0.0,
                                      p.look_sens, p.look_sens, False)
        rv3d.view_rotation = rot
        # Vertical mouse dollies forward/backward (drag up = forward, like
        # UE's LMB crash-cam), scaled by view distance.
        step = dy * rv3d.view_distance * 0.002
        cam_pos += navmath.view_forward(rot) * step
        rv3d.view_location = cam_pos + navmath.view_forward(rot) * rv3d.view_distance
        _tag_redraw(context)
        return {'RUNNING_MODAL'}

    if evalue in ('PRESS', 'RELEASE', 'CLICK', 'ANY'):
        return {'RUNNING_MODAL'}
    return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# 6) Focus (F) - frame selection and set the orbit pivot
# ---------------------------------------------------------------------------

class UE_NAV_OT_focus(bpy.types.Operator):
    bl_idname = "view3d.unreal_focus"
    bl_label = "Unreal Focus"
    bl_description = "F: focus the camera on the selection and set the orbit pivot"

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region", None) is not None
                and context.region.type == 'WINDOW')

    def invoke(self, context, event):
        if _has_selection(context):
            center = _selection_center(context)
            if center is not None:
                core.set_pivot(center)
        try:
            result = bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
        except Exception:
            result = {'CANCELLED'}
        return result


def _selection_points(context):
    """World-space points spanning the current selection (object bound
    boxes, or selected mesh elements in edit mode)."""
    try:
        context.view_layer.update()  # fresh matrix_world for new/moved objects
    except Exception:
        pass
    pts = []
    objects = getattr(context, "selected_objects", []) or []
    if 'EDIT' in (getattr(context, "mode", 'OBJECT') or 'OBJECT'):
        ob = getattr(context, "edit_object", None)
        if ob is not None and hasattr(ob.data, "vertices"):
            for v in ob.data.vertices:
                if v.select:
                    pts.append(ob.matrix_world @ v.co)
            if pts:
                return pts
    for ob in objects:
        try:
            corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
            pts.extend(corners)
        except Exception:
            pts.append(ob.matrix_world.translation)
    return pts


def _selection_center(context):
    pts = _selection_points(context)
    if not pts:
        return None
    return _median(pts)


def _instant_focus(context, rv3d):
    """Snap the view to the selection immediately (no smooth-view), so
    focus works mid-flight without fighting the fly motion."""
    if rv3d is None:
        return
    pts = _selection_points(context)
    if not pts:
        return
    center = _median(pts)
    radius = max((p - center).length for p in pts)
    core.set_pivot(center)
    rv3d.view_location = center
    if radius > 1e-6:
        rv3d.view_distance = max(navmath.MIN_DISTANCE,
                                 min(navmath.MAX_DISTANCE, radius * 2.2))
    _tag_redraw(context)


def _median(vectors):
    total = Vector((0.0, 0.0, 0.0))
    for v in vectors:
        total += v
    return total / len(vectors)


def _has_selection(context):
    if context.selected_objects:
        return True
    mode = getattr(context, "mode", 'OBJECT')
    ob = getattr(context, "edit_object", None)
    if ob is not None and 'EDIT' in mode:
        mesh_data = ob.data
        if getattr(mesh_data, "total_vert_sel", 0) > 0:
            return True
        if getattr(mesh_data, "total_edge_sel", 0) > 0:
            return True
        if getattr(mesh_data, "total_face_sel", 0) > 0:
            return True
    return False


class UE_NAV_OT_wheel_zoom(bpy.types.Operator):
    bl_idname = "view3d.unreal_wheel_zoom"
    bl_label = "Unreal Wheel Zoom"
    bl_description = "Plain mouse wheel: dolly the camera closer/farther (Unreal style)"
    bl_options = {'REGISTER', 'INTERNAL'}

    delta: bpy.props.IntProperty(default=1)

    @classmethod
    def poll(cls, context):
        return (getattr(context, "space_data", None) is not None
                and context.space_data.type == 'VIEW_3D'
                and getattr(context, "region_data", None) is not None)

    def invoke(self, context, event):
        rv3d = context.region_data
        step = 1.2 if self.delta > 0 else 1.0 / 1.2
        rv3d.view_distance = max(navmath.MIN_DISTANCE,
                                 min(navmath.MAX_DISTANCE,
                                     rv3d.view_distance * step))
        _tag_redraw(context)
        return {'FINISHED'}


class UE_NAV_OT_repair(bpy.types.Operator):
    bl_idname = "view3d.unreal_repair"
    bl_label = "Repair Default Keymaps"
    bl_description = ("Re-activate the default navigation bindings (RMB context "
                      "menu, MMB orbit) that this add-on manages - use this if "
                      "they ever stay broken after disabling the add-on")
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        from . import keymaps
        healed = keymaps.heal_keymaps(verbose=False)
        msg = "Repaired %d default keymap item(s)" % healed
        # If the user '3D View' keymap lost its factory defaults (possible
        # with prefs saved by very old versions of this add-on), restore it.
        kc = context.window_manager.keyconfigs.user
        km = kc.keymaps.get("3D View") if kc else None
        if km is not None and len(km.keymap_items) < 10:
            try:
                bpy.ops.preferences.keymap_restore(keymap="3D View")
                msg += " - '3D View' keymap restored to defaults"
            except Exception:
                pass
        self.report({'INFO'}, msg if healed or km is not None
                    else "Nothing to repair - all default items active")
        return {'FINISHED'}


classes = (
    UE_NAV_OT_fly,
    UE_NAV_OT_orbit,
    UE_NAV_OT_dolly,
    UE_NAV_OT_pan,
    UE_NAV_OT_crashcam,
    UE_NAV_OT_focus,
    UE_NAV_OT_wheel_zoom,
    UE_NAV_OT_repair,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
