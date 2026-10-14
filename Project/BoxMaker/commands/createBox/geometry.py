"""Box geometry: everything that can be worked out without Fusion.

This module is deliberately free of any adsk import. It turns a set of box
parameters into flat panel outlines and the transforms that assemble them,
as plain numbers, so the logic can be reasoned about and tested on its own.
Fusion-specific work lives in entry.py.

All lengths are in Fusion's internal units (centimetres).
"""

import math
from dataclasses import dataclass

# Fewer than three fingers along an edge does not locate the parts against
# each other, so every edge gets at least this many.
MIN_FINGER_COUNT = 3

# A finger has to be wider than the material is thick, or the thickness cut
# back at a corner eats into the finger beside it and leaves a sliver. Two
# thicknesses leaves a margin.
MIN_FINGER_WIDTH_IN_THICKNESSES = 2

# Slack for comparing lengths, in internal units (cm), which is 10 picometres.
# Dialog values do not survive the trip through millimetres exactly: 3 mm
# arrives as 0.30000000000000004, so twice it is a hair over 6 mm and an
# exactly-6 mm finger would be rejected. Far below any real tolerance.
LENGTH_TOLERANCE = 1e-9

Vector = tuple[float, float, float]
Point2D = tuple[float, float]


@dataclass(frozen=True)
class Edge:
    """The finger pattern along one edge of a panel.

    The edge is divided into `count` equal cells that alternate between
    material standing proud of the panel outline ("out") and material cut
    back by one material thickness ("in"). `starts_out` gives the state of
    the first cell; the rest alternate from there.

    Two panels meeting at a joint carry complementary patterns, so one's
    fingers fill the other's gaps.
    """
    count: int
    starts_out: bool

    def is_out(self, index: int) -> bool:
        """Whether cell `index` is material rather than gap."""
        return self.starts_out if index % 2 == 0 else not self.starts_out

    def offsets(self, thickness: float) -> list[float]:
        """How far each cell is cut back from the panel's outer edge."""
        return [0.0 if self.is_out(index) else thickness for index in range(self.count)]


@dataclass(frozen=True)
class Panel:
    """One flat part of the box, in both of the places it exists.

    `u`/`v` are the panel's flat size and `layout_origin` is where it sits in
    the shared sketch. `local_x`/`local_y`/`local_z` and `offset` say where
    the panel ends up once the box is assembled: the axes are where the
    panel's own frame points, the offset is where its origin lands.

    The four edges are named for the side they are on in flat coordinates:
    `edge_v0` is the bottom (v = 0), `edge_u1` the right (u = u), and so on.
    """
    name: str
    u: float
    v: float
    layout_origin: Point2D
    local_x: Vector
    local_y: Vector
    local_z: Vector
    offset: Vector
    edge_u0: Edge
    edge_u1: Edge
    edge_v0: Edge
    edge_v1: Edge


@dataclass
class BoxParameters:
    """The box definition, read out of the dialog.

    `finger_width` is a minimum rather than an exact size: an edge has to
    divide into a whole number of fingers, so the count is rounded down and
    the real width derived back from it. The fingers that come out are at
    least this wide, except on an edge short enough for the three finger
    minimum to bite.
    """
    length: float
    width: float
    height: float
    thickness: float
    kerf: float
    finger_width: float


def finger_count(edge_length: float, minimum_width: float, require_odd: bool = False) -> int:
    """How many fingers to divide an edge into.

    The requested finger width rarely divides the edge exactly, and a partial
    finger at the end would be useless, so the count is a whole number and
    the real finger width is derived back from it.

    Rounding is always down, which makes the requested width a minimum: the
    fingers that come out are at least as wide as asked for, never thinner.
    Rounding to nearest would treat the request as a midpoint and give
    fingers up to half a finger narrower than asked, which is the wrong way
    to miss when the width has a lower bound to respect.

    `require_odd` forces an odd count, which makes the pattern a palindrome:
    it reads the same from either end, and starts and ends in the same state.
    Top and bottom panels need that, so they present a gap at every corner
    whichever way round the edge is traversed. Stepping down to reach an odd
    count keeps the fingers wide, in line with the rounding above.

    The three finger minimum still wins over all of this. It is the one case
    where fingers can come out narrower than requested, and the only reason
    the validation rules have to look at the real width at all.
    """
    exact = edge_length / minimum_width

    # The tolerance keeps an edge that divides exactly, but arrives a hair
    # under through floating point, from losing a whole finger.
    count = math.floor(exact + LENGTH_TOLERANCE)

    if require_odd and count % 2 == 0:
        count -= 1

    count = max(count, MIN_FINGER_COUNT)

    if require_odd and count % 2 == 0:
        count += 1

    return count


def panel_outline(panel: Panel, thickness: float, kerf: float = 0.0) -> list[Point2D]:
    """The panel's flat outline as a closed loop of points.

    Walks the perimeter counter-clockwise from the bottom-left. Along each
    edge the outline steps between the outer extent and one thickness inside
    it as the finger pattern alternates.

    Corners are where the two adjacent edges meet, so the corner point simply
    takes one coordinate from each edge's end cell. A corner cut back by both
    of its edges therefore loses the full thickness square, which is what the
    neighbouring panel's finger fills.

    The laser burns away material centred on the line it follows, so every
    line is offset outward by half the kerf to compensate. That applies to
    the whole outline, not just the finger flanks, so a finger still sits
    flush against the face it meets. It leaves panels slightly interfering in
    the assembled view, which is correct: that material is what the laser
    removes.
    """
    relief = kerf / 2
    width, height = panel.u, panel.v

    bottom = _relieved_offsets(panel.edge_v0, thickness, relief)
    right = _relieved_offsets(panel.edge_u1, thickness, relief)
    top = _relieved_offsets(panel.edge_v1, thickness, relief)
    left = _relieved_offsets(panel.edge_u0, thickness, relief)

    bottom_steps = _relieved_boundaries(panel.edge_v0, width, relief)
    right_steps = _relieved_boundaries(panel.edge_u1, height, relief)
    top_steps = _relieved_boundaries(panel.edge_v1, width, relief)
    left_steps = _relieved_boundaries(panel.edge_u0, height, relief)

    points: list[Point2D] = []

    # Bottom-left corner, then left to right along the bottom edge.
    points.append((left[0], bottom[0]))
    for index, boundary in enumerate(bottom_steps):
        points.append((boundary, bottom[index]))
        points.append((boundary, bottom[index + 1]))

    # Bottom-right corner, then upwards along the right edge.
    points.append((width - right[0], bottom[-1]))
    for index, boundary in enumerate(right_steps):
        points.append((width - right[index], boundary))
        points.append((width - right[index + 1], boundary))

    # Top-right corner, then right to left along the top edge.
    points.append((width - right[-1], height - top[-1]))
    for index in reversed(range(len(top_steps))):
        boundary = top_steps[index]
        points.append((boundary, height - top[index + 1]))
        points.append((boundary, height - top[index]))

    # Top-left corner, then downwards along the left edge back to the start.
    points.append((left[-1], height - top[0]))
    for index in reversed(range(len(left_steps))):
        boundary = left_steps[index]
        points.append((left[index + 1], boundary))
        points.append((left[index], boundary))

    return _without_repeats(points)


def _relieved_offsets(edge: Edge, thickness: float, relief: float) -> list[float]:
    """Cell cut-back depths, moved outward by the kerf relief.

    A finger reaches `relief` beyond the panel's nominal extent, and a gap
    stops `relief` short of its nominal depth.
    """
    return [offset - relief for offset in edge.offsets(thickness)]


def _relieved_boundaries(edge: Edge, edge_length: float, relief: float) -> list[float]:
    """Where the outline steps between cells, moved outward by the relief.

    Cells alternate strictly, so each boundary has a finger on exactly one
    side. The boundary moves towards the gap, widening the finger by `relief`
    and narrowing the gap by the same amount on each of its sides.
    """
    cell = edge_length / edge.count
    return [
        (index + 1) * cell + (relief if edge.is_out(index) else -relief)
        for index in range(edge.count - 1)
    ]


def _without_repeats(points: list[Point2D], tolerance: float = 1e-9) -> list[Point2D]:
    """Drop points that repeat the previous one, including around the join.

    Two adjacent cells in the same state produce a zero-length step. Those
    would become degenerate sketch lines, which Fusion rejects.
    """
    cleaned: list[Point2D] = []
    for point in points:
        if cleaned and _same_point(cleaned[-1], point, tolerance):
            continue
        cleaned.append(point)

    while len(cleaned) > 1 and _same_point(cleaned[0], cleaned[-1], tolerance):
        cleaned.pop()

    return cleaned


def _same_point(first: Point2D, second: Point2D, tolerance: float) -> bool:
    return abs(first[0] - second[0]) < tolerance and abs(first[1] - second[1]) < tolerance


def outline_bounds(outline: list[Point2D]) -> tuple[float, float, float, float]:
    """The outline's bounding box as (min u, min v, max u, max v)."""
    return (
        min(u for u, _ in outline),
        min(v for _, v in outline),
        max(u for u, _ in outline),
        max(v for _, v in outline),
    )


def actual_finger_width(params: "BoxParameters") -> dict[str, float]:
    """The real finger width along each of the three directions.

    The requested finger width is a minimum. Rounding down means the real
    fingers are at least that wide, with one exception: a short edge forced
    up to the three finger minimum, where they come out narrower.
    """
    return {
        'length': params.length / finger_count(params.length, params.finger_width, require_odd=True),
        'width': params.width / finger_count(params.width, params.finger_width, require_odd=True),
        'height': params.height / finger_count(params.height, params.finger_width),
    }


def validate(params: "BoxParameters", format_length=None) -> list[str]:
    """Everything wrong with these parameters, worst first.

    An empty list means the box can be built. Messages are kept to a few
    words so they fit the dialog.

    Both real rules are expressed in material thicknesses, because that is
    what the failure is about: a corner is cut back by one thickness, so a
    finger narrower than about two thicknesses gets eaten into and leaves a
    sliver. Neither rule is expressed in the requested finger width, which
    is not what binds. On a short edge the three finger minimum overrides
    the request entirely, so a box can be too small to finger at any
    requested width.

    Since finger_count rounds down, the real fingers are at least as wide as
    requested except when that minimum bites, and the two rules below cover
    both cases. The check on the real width afterwards should therefore
    never fire; it is kept as a backstop against changes to finger_count.
    """
    format_length = format_length or _format_millimetres
    messages: list[str] = []

    if min(params.length, params.width, params.height) <= 0:
        messages.append('Box dimensions must be positive')
    if params.thickness <= 0:
        messages.append('Material thickness must be positive')
    if params.finger_width <= 0:
        messages.append('Finger width must be positive')
    if params.kerf < 0:
        messages.append('Kerf cannot be negative')

    # Everything below divides by these, so stop while they are unusable.
    if messages:
        return messages

    shortest = min(params.length, params.width, params.height)
    narrowest_allowed = MIN_FINGER_WIDTH_IN_THICKNESSES * params.thickness

    if not _less(params.thickness * 2, shortest):
        messages.append('Walls thicker than the box')

    # Three fingers at the narrowest they are allowed to be. Below this no
    # requested finger width can rescue the box; the material has to get
    # thinner or the box bigger.
    smallest_box = MIN_FINGER_COUNT * narrowest_allowed
    fingers_explained = True
    if _less(shortest, smallest_box):
        messages.append(f'Box needs {format_length(smallest_box)} sides')
    elif _less(params.finger_width, narrowest_allowed):
        messages.append(f'Fingers need {format_length(narrowest_allowed)} minimum')
    else:
        fingers_explained = False

    # The backstop, and only worth hearing from when the rules above found
    # nothing: otherwise it just restates what has already been said.
    narrowest = min(actual_finger_width(params).values())
    if not fingers_explained and _less(narrowest, narrowest_allowed):
        messages.append(f'Fingers {format_length(narrowest)}, need {format_length(narrowest_allowed)}')

    if not _less(params.kerf, narrowest):
        messages.append('Kerf wider than a finger')

    return messages


def _less(value: float, limit: float) -> bool:
    """Whether a length is meaningfully below a limit."""
    return value < limit - LENGTH_TOLERANCE


def _format_millimetres(value: float) -> str:
    """Fallback formatter, for use outside Fusion."""
    return f'{value * 10:.2f} mm'


def panel_layout(params: BoxParameters, layout_gap_min: float) -> list[Panel]:
    """Describe the six panels: flat size, sketch position, and placement.

    The assembled box occupies x in [0, length], y in [0, width],
    z in [0, height]. Every panel is the full size of the face it covers;
    the finger patterns decide which material each one actually keeps, so
    neighbouring panels interlock rather than interfere.

    The four walls are ordered counter-clockwise seen from above -- front,
    right, back, left -- and each one carries fingers on its first edge and
    gaps on its last. Going around the loop, every joint therefore pairs a
    finger side with a gap side, and all four walls are the same part. A
    mirrored arrangement, with two opposite walls running the same way, would
    leave two corners where both panels claim the same material.
    """
    length, width, height = params.length, params.width, params.height
    thickness = params.thickness

    # Vertical joints run the full height and are shared by two walls, so the
    # count is the same for all four. No parity constraint: these joints are
    # always read in the same direction by both panels.
    vertical = finger_count(height, params.finger_width)

    # Horizontal joints meet the top and bottom panels. Odd counts make the
    # pattern a palindrome, which is what lets the lid read an edge from
    # either end and still mate.
    along_length = finger_count(length, params.finger_width, require_odd=True)
    along_width = finger_count(width, params.finger_width, require_odd=True)

    # Lids present a gap at every corner, because whichever wall owns that
    # corner has a finger there.
    lid_length = Edge(along_length, starts_out=False)
    lid_width = Edge(along_width, starts_out=False)

    # Walls present the complement: a finger at both ends of every horizontal
    # edge, fingers on the first vertical edge and gaps on the last.
    wall_length = Edge(along_length, starts_out=True)
    wall_width = Edge(along_width, starts_out=True)
    wall_first = Edge(vertical, starts_out=True)
    wall_last = Edge(vertical, starts_out=False)

    x_axis: Vector = (1.0, 0.0, 0.0)
    y_axis: Vector = (0.0, 1.0, 0.0)
    z_axis: Vector = (0.0, 0.0, 1.0)

    # Each panel is drawn flat and extruded in +z, so its local frame is
    # (u, v, thickness). The axes below are where that frame points once the
    # panel is stood up; local_z is always local_x x local_y, and the offset
    # accounts for whether that leaves the thickness pointing in or out.
    specs = [
        # name,  u,      v,      x,       y,       z,       offset,                     u0,         u1,        v0,          v1
        ('Bottom', length, width, x_axis, y_axis, z_axis, (0.0, 0.0, 0.0),
         lid_width, lid_width, lid_length, lid_length),
        ('Top', length, width, x_axis, y_axis, z_axis, (0.0, 0.0, height - thickness),
         lid_width, lid_width, lid_length, lid_length),
        ('Front', length, height, x_axis, z_axis, (0.0, -1.0, 0.0), (0.0, thickness, 0.0),
         wall_first, wall_last, wall_length, wall_length),
        ('Right', width, height, y_axis, z_axis, x_axis, (length - thickness, 0.0, 0.0),
         wall_first, wall_last, wall_width, wall_width),
        ('Back', length, height, (-1.0, 0.0, 0.0), z_axis, y_axis, (length, width - thickness, 0.0),
         wall_first, wall_last, wall_length, wall_length),
        ('Left', width, height, (0.0, -1.0, 0.0), z_axis, (-1.0, 0.0, 0.0), (thickness, width, 0.0),
         wall_first, wall_last, wall_width, wall_width),
    ]

    # Lay the panels out in a row in the sketch, separated by a visible gap.
    # Every outline grows by half a kerf on each side, so the gap allows for
    # that too, keeping the panels from touching.
    gap = max(layout_gap_min, thickness * 2) + params.kerf
    panels = []
    cursor = 0.0
    for name, u, v, local_x, local_y, local_z, offset, edge_u0, edge_u1, edge_v0, edge_v1 in specs:
        panels.append(Panel(
            name=name,
            u=u,
            v=v,
            layout_origin=(cursor, 0.0),
            local_x=local_x,
            local_y=local_y,
            local_z=local_z,
            offset=offset,
            edge_u0=edge_u0,
            edge_u1=edge_u1,
            edge_v0=edge_v0,
            edge_v1=edge_v1,
        ))
        cursor += u + gap

    return panels
