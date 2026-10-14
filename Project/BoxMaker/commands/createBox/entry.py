import adsk.core
import adsk.fusion
import os

from ...lib import fusionAddInUtils as futil
from ... import config
from .geometry import (
    BoxParameters, Panel, Point2D,
    panel_layout, panel_outline, outline_bounds, validate,
)

app = adsk.core.Application.get()
ui = app.userInterface


# *** Command identity information. ***
CMD_ID = "create_Box"
CMD_NAME = 'Create Box'
CMD_Description = 'Generate a laser-cut box with finger joints'

# Specify that the command will be promoted to the panel.
IS_PROMOTED = True

# *** Location where the command button will be created. ***
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidCreatePanel'
COMMAND_BESIDE_ID = 'PrimitiveBox'

# Resource location for command icons.
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

# Input ids, kept in one place so the dialog and the readers can't drift apart.
INPUT_LENGTH = 'box_length'
INPUT_WIDTH = 'box_width'
INPUT_HEIGHT = 'box_height'
INPUT_THICKNESS = 'material_thickness'
INPUT_KERF = 'kerf'
INPUT_FINGER_WIDTH = 'finger_width'
INPUT_OPEN_TOP = 'open_top'
INPUT_MESSAGES = 'validation_messages'

# Names given to the geometry this command creates.
LAYOUT_SKETCH_NAME = 'Flat Layout'
TIMELINE_GROUP_NAME = 'Box'

# Spacing between panel outlines in the flat layout, in internal units (cm).
LAYOUT_GAP_MIN = 0.5

# How closely a profile's bounding box must match a panel's layout rectangle
# to be considered its profile, in internal units (cm). Far tighter than the
# gap between panels, so there is no ambiguity.
PROFILE_MATCH_TOLERANCE = 1e-6

# How far a transform must differ from the identity before it is worth moving
# a panel. Mixes lengths (cm) and direction cosines, but both are far larger
# than this for any real placement.
TRANSFORM_TOLERANCE = 1e-9

# Height of the validation message box, in rows. Fixed, so the dialog does
# not jump about as messages come and go.
MESSAGE_ROWS = 3

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    command_control = panel.controls.itemById(CMD_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Delete the button command control
    if command_control:
        command_control.deleteMe()

    # Delete the command definition
    if command_definition:
        command_definition.deleteMe()


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    # https://help.autodesk.com/view/fusion360/ENU/?contextId=CommandInputs
    inputs = args.command.commandInputs
    units = app.activeProduct.unitsManager.defaultLengthUnits

    def value_input(input_id: str, name: str, default: str):
        return inputs.addValueInput(input_id, name, units, adsk.core.ValueInput.createByString(default))

    # Outer dimensions of the box.
    value_input(INPUT_LENGTH, 'Length', '100 mm')
    value_input(INPUT_WIDTH, 'Width', '80 mm')
    value_input(INPUT_HEIGHT, 'Height', '50 mm')

    # Material and machine properties.
    value_input(INPUT_THICKNESS, 'Material Thickness', '3 mm')
    value_input(INPUT_KERF, 'Kerf', '0.15 mm')

    # Joint settings.
    value_input(INPUT_FINGER_WIDTH, 'Min Finger Width', '10 mm')

    # Style.
    inputs.addBoolValueInput(INPUT_OPEN_TOP, 'Open Top', True, '', False)

    # Sits at the bottom of the dialog and stays hidden until something is
    # wrong, so a valid box shows no clutter. No label, so it spans the
    # full width.
    inputs.addTextBoxCommandInput(INPUT_MESSAGES, '', '', MESSAGE_ROWS, True)
    refresh_messages(inputs)

    # Connect to the events that are needed by this command.
    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.validateInputs, command_validate_input, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def format_length(value: float) -> str:
    """Format an internal length the way the document displays lengths."""
    units = app.activeProduct.unitsManager
    return units.formatValue(value, units.defaultLengthUnits)


def problems(inputs: adsk.core.CommandInputs) -> list[str]:
    """Everything wrong with the dialog as it stands."""
    return validate(read_parameters(inputs), format_length)


def refresh_messages(inputs: adsk.core.CommandInputs):
    """Show or hide the message box to match the current inputs."""
    message_box: adsk.core.TextBoxCommandInput = inputs.itemById(INPUT_MESSAGES)
    if message_box is None:
        return

    messages = problems(inputs)
    message_box.isVisible = bool(messages)
    if messages:
        lines = '<br/>'.join(messages)
        message_box.formattedText = f'<span style="color:#c00">{lines}</span>'


def read_parameters(inputs: adsk.core.CommandInputs) -> BoxParameters:
    """Collect the dialog values into a plain parameter object."""
    def value(input_id: str) -> float:
        command_input: adsk.core.ValueCommandInput = inputs.itemById(input_id)
        return command_input.value

    return BoxParameters(
        length=value(INPUT_LENGTH),
        width=value(INPUT_WIDTH),
        height=value(INPUT_HEIGHT),
        thickness=value(INPUT_THICKNESS),
        kerf=value(INPUT_KERF),
        finger_width=value(INPUT_FINGER_WIDTH),
        open_top=inputs.itemById(INPUT_OPEN_TOP).value,
    )


def draw_panels(sketch: adsk.fusion.Sketch, panels: list[Panel],
                outlines: dict[str, list[Point2D]]):
    """Draw each panel's outline into the single flat sketch.

    Lines are chained end to start so Fusion sees one closed loop. Passing
    fresh Point3D objects for both ends would leave coincident but unjoined
    points, and no profile would form.

    This is the slowest part of building a box: each call into the sketch
    API costs roughly a millisecond whatever it does, and a finely divided
    box runs to a thousand lines. Two things have been measured and were
    not worth keeping. Drawing each distinct shape once and duplicating it
    with Sketch.copy halves the number of calls but runs about half again
    slower, because one copy of a few hundred entities costs more than
    drawing them. Deferring compute, which is done here, is worth it: the
    solve itself is only about a tenth of the cost of adding the lines.

    Compute is deferred for the duration: without it Fusion re-solves the
    sketch and rebuilds its profiles after every single line.
    """
    sketch.isComputeDeferred = True
    try:
        lines = sketch.sketchCurves.sketchLines
        for panel in panels:
            origin_x, origin_y = panel.layout_origin
            points = [
                adsk.core.Point3D.create(origin_x + u, origin_y + v, 0)
                for u, v in outlines[panel.name]
            ]

            first = lines.addByTwoPoints(points[0], points[1])
            previous = first
            for point in points[2:]:
                previous = lines.addByTwoPoints(previous.endSketchPoint, point)
            lines.addByTwoPoints(previous.endSketchPoint, first.startSketchPoint)
    finally:
        # Restored even on failure: a sketch left deferred never rebuilds its
        # profiles, and the next step looks them up.
        sketch.isComputeDeferred = False


def find_profile(sketch: adsk.fusion.Sketch, panel: Panel,
                 outline: list[Point2D]) -> adsk.fusion.Profile:
    """Find the profile belonging to a panel by its bounding box.

    Matched against the bounds of the outline actually drawn, so kerf relief
    is accounted for without having to re-derive it. A bounding box is a
    sturdier handle than the centroid, which shifts as fingers are added, and
    than the index, which is not ordered and will shift again once the user
    edits the sketch.
    """
    origin_x, origin_y = panel.layout_origin
    min_u, min_v, max_u, max_v = outline_bounds(outline)
    expected = (origin_x + min_u, origin_y + min_v, origin_x + max_u, origin_y + max_v)

    for profile in sketch.profiles:
        box = profile.boundingBox
        actual = (box.minPoint.x, box.minPoint.y, box.maxPoint.x, box.maxPoint.y)
        if all(abs(a - b) < PROFILE_MATCH_TOLERANCE for a, b in zip(actual, expected)):
            return profile

    raise RuntimeError(
        f'No sketch profile found for the {panel.name} panel. '
        f'The sketch has {sketch.profiles.count} profiles; expected one per panel. '
        f'Looked for bounds {expected}.'
    )


def extrude_panel(component: adsk.fusion.Component, profile: adsk.fusion.Profile,
                  panel: Panel, thickness: float) -> adsk.fusion.BRepBody:
    """Extrude one panel profile to material thickness as its own body."""
    extrudes = component.features.extrudeFeatures
    extrude_input = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extrude_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(thickness))
    extrude = extrudes.add(extrude_input)

    body = extrude.bodies.item(0)
    body.name = panel.name
    return body


def is_identity(transform: adsk.core.Matrix3D) -> bool:
    """Whether a transform would move nothing."""
    identity = adsk.core.Matrix3D.create()
    return all(
        abs(value - reference) < TRANSFORM_TOLERANCE
        for value, reference in zip(transform.asArray(), identity.asArray())
    )


def place_panel(component: adsk.fusion.Component, body: adsk.fusion.BRepBody, panel: Panel):
    """Move an extruded panel from its flat layout position into the assembly.

    The panel is currently sitting at its layout origin on the XY plane. The
    move has to be a single rigid transform, so rather than translating and
    then rotating, the rotation is applied about the sketch origin and the
    translation is pre-compensated for where that rotation sends the panel.
    """
    origin_x, origin_y = panel.layout_origin

    # Where the rotation alone would send the panel's layout origin.
    rotated_origin = [
        origin_x * panel.local_x[axis] + origin_y * panel.local_y[axis]
        for axis in range(3)
    ]

    transform = adsk.core.Matrix3D.create()
    transform.setWithCoordinateSystem(
        adsk.core.Point3D.create(*[panel.offset[axis] - rotated_origin[axis] for axis in range(3)]),
        adsk.core.Vector3D.create(*panel.local_x),
        adsk.core.Vector3D.create(*panel.local_y),
        adsk.core.Vector3D.create(*panel.local_z),
    )

    # A panel that is already where it belongs needs no move, and Fusion
    # rejects an identity transform as invalid rather than ignoring it.
    if is_identity(transform):
        return

    bodies = adsk.core.ObjectCollection.create()
    bodies.add(body)

    move_features = component.features.moveFeatures
    move_input = move_features.createInput2(bodies)
    move_input.defineAsFreeMove(transform)
    move_features.add(move_input)


def group_timeline_entries(timeline: adsk.fusion.Timeline, start_index: int):
    """Collapse everything created since start_index into one timeline group.

    A box is a dozen or so features; left loose they bury whatever else is in
    the user's timeline.
    """
    end_index = timeline.markerPosition - 1
    if end_index <= start_index:
        return

    group = timeline.timelineGroups.add(start_index, end_index)
    group.name = TIMELINE_GROUP_NAME


def build_box(params: BoxParameters, design: adsk.fusion.Design, is_preview: bool):
    """Build the box geometry.

    This is the single code path behind both the live preview and the final
    result. `is_preview` is for the few places where the two legitimately
    differ -- naming components, attaching parameters, cosmetic detail that
    isn't worth recomputing on every keystroke. The geometry itself should be
    identical either way, so that what the user sees is what they get.

    The flat sketch is the real output: everything downstream is driven by it,
    so that a user editing a panel outline sees the assembled box update.
    """
    futil.log(f'{CMD_NAME} build_box (preview={is_preview}) {params}')

    panels = panel_layout(params, LAYOUT_GAP_MIN)

    # The panels are plain bodies in the root component: this is a part design,
    # not an assembly.
    component = design.rootComponent

    # Remember where the timeline was so everything below can be collapsed
    # into one group afterwards.
    timeline = design.timeline
    start_index = timeline.markerPosition

    # Worked out once and reused: the outline decides both what is drawn and
    # which profile belongs to which panel.
    outlines = {
        panel.name: panel_outline(panel, params.thickness, params.kerf)
        for panel in panels
    }

    sketch = component.sketches.add(component.xYConstructionPlane)
    sketch.name = LAYOUT_SKETCH_NAME
    # Note: do not set sketch.areProfilesShown = False to speed this up. It
    # does not merely hide the shading, it stops Fusion computing profiles at
    # all, and the extrudes then have nothing to work from.
    draw_panels(sketch, panels, outlines)

    for panel in panels:
        profile = find_profile(sketch, panel, outlines[panel.name])
        body = extrude_panel(component, profile, panel, params.thickness)
        place_panel(component, body, panel)

    group_timeline_entries(timeline, start_index)

    return component


# This event handler is called when the command needs to compute a new preview in the graphics window.
def command_preview(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Preview Event')

    design = adsk.fusion.Design.cast(app.activeProduct)
    build_box(read_parameters(args.command.commandInputs), design, is_preview=True)

    # The preview is a faithful build, so Fusion can keep it as the result
    # instead of discarding it and firing execute again.
    args.isValidResult = True


# This event handler is called when the user clicks the OK button in the command dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')

    # Only reached when the preview did not already produce a valid result
    # (for example if the preview was skipped). Same code path either way.
    design = adsk.fusion.Design.cast(app.activeProduct)
    build_box(read_parameters(args.command.commandInputs), design, is_preview=False)


# This event handler is called when the user changes anything in the command dialog
# allowing you to modify values of other inputs based on that change.
def command_input_changed(args: adsk.core.InputChangedEventArgs):
    changed_input = args.input
    inputs = args.inputs

    futil.log(f'{CMD_NAME} Input Changed Event fired from a change to {changed_input.id}')

    # Validation runs here as well as in validateInputs, because that handler
    # should not be reaching back into the dialog to change other inputs.
    refresh_messages(inputs)


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    # Blocks both OK and the preview, so a box that cannot be built is never
    # attempted. The reasons are shown by refresh_messages.
    args.areInputsValid = not problems(args.inputs)


# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')

    global local_handlers
    local_handlers = []
