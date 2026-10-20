"""Appearances: making a built box look like it came off a laser cutter.

A laser leaves two quite different surfaces on one panel. The faces that were
the sheet keep the material's own look, and every cut edge comes back dark and
slightly glazed from the burn. Giving a body one appearance cannot show both,
so a panel is painted in two passes: the whole body dark, then the two sheet
faces overridden back to the material.

A library appearance cannot be assigned to a body directly. It has to be
copied into the document first, and the copy is what gets applied.

Nothing here is allowed to fail a build. A missing appearance is a cosmetic
disappointment, not a reason to lose a box, so a lookup that comes up empty
is logged and the panels are left with their default look.
"""

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil

app = adsk.core.Application.get()


# The library holding the stock appearances. Fusion has renamed it, so both
# names are tried before giving up.
LIBRARY_NAMES = ('Fusion Appearance Library', 'Fusion 360 Appearance Library')

# The burnt faces left by the beam. Rough rather than glossy: a cut edge is
# matte and slightly fibrous, not lacquered.
CUT_APPEARANCE_NAME = 'Powder Coat - Rough (Black)'

# The faces that were the surface of the sheet.
SHEET_APPEARANCE_NAME = 'MDF Board'

# How closely a face normal must line up with the sketch normal to count as a
# face of the sheet rather than a cut edge. Generous, because the only other
# faces on a panel are exactly perpendicular to it.
NORMAL_TOLERANCE = 1e-3


def _library() -> adsk.core.MaterialLibrary:
    """The appearance library, under whichever name this Fusion uses."""
    for name in LIBRARY_NAMES:
        library = app.materialLibraries.itemByName(name)
        if library:
            return library

    installed = ', '.join(
        app.materialLibraries.item(index).name
        for index in range(app.materialLibraries.count)
    )
    futil.log(f'No appearance library found. Installed libraries: {installed}')
    return None


def _appearance(design: adsk.fusion.Design, library: adsk.core.MaterialLibrary,
                name: str) -> adsk.core.Appearance:
    """Get an appearance that bodies in `design` can be given.

    The copy is reused on later builds, so repeated previews do not litter
    the document with numbered duplicates.
    """
    existing = design.appearances.itemByName(name)
    if existing:
        return existing

    source = library.appearances.itemByName(name)
    if source is None:
        futil.log(f'Appearance "{name}" is not in the {library.name}.')
        return None

    return design.appearances.addByCopy(source, name)


class Painter:
    """Paints freshly extruded panels so they read as laser-cut sheet.

    Each panel is painted while it is still lying flat, between its extrude
    and the move that stands it up. Appearance is not parametric, so an
    override does not belong to a point in the timeline: it is pinned to the
    faces it was applied to. Painting after the move pins the sheet faces of
    the moved panel, and rolling the timeline back to before that move leaves
    those faces unpainted and the panel entirely black. Painting first means
    the override rides along through the move, and the box looks right
    wherever the marker sits.

    Lying flat is also what makes the sheet faces easy to name: a panel is
    extruded along z, so they are the only faces square to it. Which way
    along z does not matter here -- both sheet faces get the same
    appearance -- so this is indifferent to the extrude direction.
    """

    def __init__(self, cut: adsk.core.Appearance, sheet: adsk.core.Appearance):
        self._cut = cut
        self._sheet = sheet
        self._sheet_normal = adsk.core.Vector3D.create(0, 0, 1)

    def paint(self, body: adsk.fusion.BRepBody):
        """Paint one panel, still flat in the sketch plane."""
        if self._cut:
            body.appearance = self._cut
        if self._sheet:
            for face in self._sheet_faces(body):
                face.appearance = self._sheet

    def _sheet_faces(self, body: adsk.fusion.BRepBody) -> list[adsk.fusion.BRepFace]:
        """The two faces of a panel that were the surfaces of the sheet.

        Everything else on the panel -- its sides, and the walls of every
        finger and notch -- runs parallel to the extrude axis and was made by
        the beam.
        """
        faces = []
        for index in range(body.faces.count):
            face = body.faces.item(index)
            plane = adsk.core.Plane.cast(face.geometry)
            if plane is None:
                continue
            if abs(abs(plane.normal.dotProduct(self._sheet_normal)) - 1.0) < NORMAL_TOLERANCE:
                faces.append(face)
        return faces


def painter(design: adsk.fusion.Design, enabled: bool) -> Painter:
    """A painter for this build.

    Always returns something to paint with, so the build has no cosmetic
    branch in it. When appearances are switched off, or none of them can be
    found, the painter is simply one that does nothing.
    """
    library = _library() if enabled else None
    if library is None:
        return Painter(None, None)

    return Painter(
        _appearance(design, library, CUT_APPEARANCE_NAME),
        _appearance(design, library, SHEET_APPEARANCE_NAME),
    )
