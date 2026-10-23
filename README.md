# Unreal Engine Navigation for Blender

A Blender add-on that replicates **Unreal Engine's** viewport navigation with
precision — based on Epic Games' official documentation
("Viewport Controls in Unreal Engine"). Built for people who move between
Unity/Unreal and Blender and want the same muscle memory to just work,
without relearning navigation from scratch.

Compatible with Blender 4.2 through 5.2 (tested on 5.2.1 LTS).

## Installation

1. Grab `ue_navigation.zip` from this package.
2. In Blender: `Edit → Preferences → Add-ons → Install…` and select the zip file.
3. Enable the add-on (name: **Unreal Engine Navigation**).

## Controls (matching Unreal Engine exactly)

### Fly & Look — hold Right Mouse Button (Perspective view)

| Key | Action |
|---|---|
| **Hold RMB + move mouse** | Free Look (angle-clamped, same as UE) |
| **RMB + W/S/A/D** (or arrow keys / Numpad 8·2·4·6) | Fly forward/back/left/right |
| **RMB + E / Q** (or Page Up / Page Down / Numpad 9·7) | Move up/down in **world** space |
| **RMB + R / F** | Move up/down in **camera-local** space |
| **RMB + Z / C** (or Numpad 1·3) | Increase/decrease Field of View |
| **RMB + mouse wheel** | Increase/decrease **fly speed** (shown in HUD) |
| **LMB while RMB is held + drag** | Pan up/down/left/right |
| **Release RMB** | Exit fly mode |

### Orthographic view
- **RMB + drag** = Pan (as in UE; the fly-mode keys map to pan here).

### Pivot-based orbit navigation (Unreal's Alt+LMB behavior)

| Key | Action |
|---|---|
| **F** | Focus on selection (as in UE) and set the **pivot point** |
| **Alt + LMB + drag** | Orbit/turntable rotation around the pivot — around the selected object if one is selected, otherwise around the last pivot (from F or the current view center) |
| **Alt + RMB + drag** | Dolly (move closer/farther) around the pivot (selected or last-used) |
| **Alt + MMB + drag** or **MMB + drag** | Track/pan in the direction of mouse movement |
| **Mouse wheel (no button held)** | Zoom (as in UE) |
| **ESC during Orbit/Dolly** | Revert to the previous view (undoes the movement) |
| **Right-click (no drag)** | Blender's context menu — works as before |

### Optional preferences
- **LMB drag crash-cam** (Unreal's perspective LMB behavior: drag forward/back + rotate)
  — off by default, since Blender 5 reserves that same drag gesture for
  selection/transform.
- Look/Orbit/Dolly sensitivity, base fly speed, FOV speed, and axis inversion.
- MMB-pan, the F key, and the HUD can each be toggled off independently.

## Technical notes

- On activation, the add-on temporarily disables a few conflicting default
  shortcuts (MMB rotation and the RMB context-menu handlers). On
  deactivation it restores all of them through a pattern-based, stateless
  repair — so even if preferences were saved mid-session, a simple
  enable/disable cycle fixes everything.
- If the right-click menu or MMB rotation ever stop working for any reason,
  go to `Edit → Preferences → Add-ons → Unreal Engine Navigation` and click
  **"Repair default keymaps"**.
- A right-click with no drag (tolerating up to ~12 px of hand jitter) opens
  the context menu for the current mode (Object Mode, Edit Mode, etc.).
  This call is deferred to the next main-loop tick so the popup opens
  correctly.
- Keymap installation is delayed until the user's keyconfig is fully ready
  (retrying for up to ~30 seconds) to avoid failures caused by installing
  too early during startup.
- The orbit pivot behaves like UE's "point of interest": pressing F on any
  selection makes the next orbit rotate around that same point.
