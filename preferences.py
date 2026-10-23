# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences."""

import bpy

from . import keymaps


def _refresh_keymaps(self, context):
    keymaps.refresh()


class UE_NAV_PG_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    look_sens: bpy.props.FloatProperty(
        name="Look / Fly Sensitivity",
        description="Degrees of rotation per pixel while looking around with RMB held",
        default=0.10, min=0.01, max=1.0)

    orbit_sens: bpy.props.FloatProperty(
        name="Orbit Sensitivity",
        description="Degrees of orbit per pixel during Alt+LMB orbit",
        default=0.25, min=0.01, max=2.0)

    dolly_sens: bpy.props.FloatProperty(
        name="Dolly Sensitivity",
        description="Zoom response per pixel during Alt+RMB dolly (exponential)",
        default=0.004, min=0.0005, max=0.02, precision=4, subtype='FACTOR')

    fly_base_speed: bpy.props.FloatProperty(
        name="Fly Speed (level 4)",
        description="Flight speed in units/second at speed level 4 (UE default)",
        default=12.0, min=0.1, max=1000.0, subtype='DISTANCE')

    fly_default_level: bpy.props.IntProperty(
        name="Default Speed Level",
        description="Initial flight speed level (1-15, like UE's camera speed setting)",
        default=4, min=1, max=15)

    fov_rate: bpy.props.FloatProperty(
        name="FOV Rate",
        description="Lens change per second while holding RMB + Z/C (mm/s)",
        default=40.0, min=1.0, max=500.0)

    invert_look_y: bpy.props.BoolProperty(
        name="Invert Look Y",
        description="Invert vertical look direction with RMB held",
        default=False)

    invert_orbit_x: bpy.props.BoolProperty(
        name="Invert Orbit X",
        description="Invert horizontal orbit direction (Alt+LMB)",
        default=False)

    invert_orbit_y: bpy.props.BoolProperty(
        name="Invert Orbit Y",
        description="Invert vertical orbit direction (Alt+LMB)",
        default=False)

    mmb_pan: bpy.props.BoolProperty(
        name="MMB drag pans (Unreal)",
        description=("Middle-mouse drag tracks the camera like UE and disables "
                     "Blender's default MMB orbit (orbit stays on Alt+LMB). "
                     "Restored when the add-on is disabled"),
        default=True,
        update=_refresh_keymaps)

    focus_key: bpy.props.BoolProperty(
        name="F focuses selection",
        description=("F frames the selection and sets the orbit pivot (UE). "
                     "Edit-mode F shortcuts are not affected"),
        default=True,
        update=_refresh_keymaps)

    crash_cam: bpy.props.BoolProperty(
        name="LMB drag crash-cam (experimental)",
        description=("UE perspective behavior: LMB drag moves forward/backward "
                     "and rotates. Off by default because Blender 5 reserves "
                     "LMB-drag for box-select and drag-to-move"),
        default=False,
        update=_refresh_keymaps)

    show_hud: bpy.props.BoolProperty(
        name="Show speed HUD",
        description="Show the flight speed overlay while flying (like UE's speed widget)",
        default=True)

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Unreal Engine Viewport Navigation", icon='VIEW3D')
        box.label(text="RMB hold: WASD fly · Q/E global up/down · R/F local up/down",
                  icon='MOUSE_RMB')
        box.label(text="RMB + wheel: speed · Z/C: FOV · F: focus · Alt+LMB: orbit",
                  icon='MOUSE_LMB')
        box.label(text="Alt+RMB: dolly · Alt+MMB / MMB: pan", icon='MOUSE_MMB')

        col = layout.column()
        col.prop(self, "look_sens")
        col.prop(self, "orbit_sens")
        col.prop(self, "dolly_sens")
        col.separator()
        col.prop(self, "fly_base_speed")
        col.prop(self, "fly_default_level")
        col.prop(self, "fov_rate")
        col.separator()
        row = col.row()
        row.prop(self, "invert_look_y")
        row.prop(self, "invert_orbit_x")
        row.prop(self, "invert_orbit_y")
        col.separator()
        col.prop(self, "mmb_pan")
        col.prop(self, "focus_key")
        col.prop(self, "crash_cam")
        col.prop(self, "show_hud")

        row = layout.row()
        row.operator("view3d.unreal_repair",
                     text="Repair default keymaps (RMB menu / MMB orbit)")
        row.label(text="    ← if right-click menu ever stops working")


classes = (UE_NAV_PG_preferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
