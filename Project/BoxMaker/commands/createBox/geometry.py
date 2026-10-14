"""Box geometry: everything that can be worked out without Fusion.

This module is deliberately free of any adsk import. It turns a set of box
parameters into flat panel outlines and the transforms that assemble them,
as plain numbers, so the logic can be reasoned about and tested on its own.
Fusion-specific work lives in entry.py.

All lengths are in Fusion's internal units (centimetres).
"""

from dataclasses import dataclass

# Fewer than three fingers along an edge does not locate the parts against
# each other, so every edge gets at least this many.
MIN_FINGER_COUNT = 3

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

    `finger_width` is a target rather than a promise: an edge has to divide
    into a whole number of fingers, so the real width is derived from the
    count that best fits.
    """
    length: float
    width: float
    height: float
    thickness: float
    kerf: float
    finger_width: float


def finger_count(edge_length: float, nominal_width: float, require_odd: bool = False) -> int:
    """How many fingers to divide an edge into.

    The nominal finger width rarely divides the edge exactly, and a partial
    finger at the end would be useless, so the count is rounded to the
    nearest whole number and the real finger width derived back from it.

    `require_odd` forces an odd count, which makes the pattern a palindrome:
    it reads the same from either end, and starts and ends in the same state.
    Top and bottom panels need that, so they present a gap at every corner
    whichever way round the edge is traversed.
    """
    exact = edge_length / nominal_width
    count = max(1, round(exact))

    if require_odd and count % 2 == 0:
        # Step to whichever neighbouring odd count is closer to the ideal.
        count = count + 1 if exact > count else count - 1

    count = max(count, MIN_FINGER_COUNT)

    if require_odd and count % 2 == 0:
        count += 1

    return count


def panel_outline(panel: Panel, thickness: float) -> list[Point2D]:
    """The panel's flat outline as a closed loop of points.

    Walks the perimeter counter-clockwise from the bottom-left. Along each
    edge the outline steps between the outer extent and one thickness inside
    it as the finger pattern alternates.

    Corners are where the two adjacent edges meet, so the corner point simply
    takes one coordinate from each edge's end cell. A corner cut back by both
    of its edges therefore loses the full thickness square, which is what the
    neighbouring panel's finger fills.
    """
    width, height = panel.u, panel.v
    bottom = panel.edge_v0.offsets(thickness)
    right = panel.edge_u1.offsets(thickness)
    top = panel.edge_v1.offsets(thickness)
    left = panel.edge_u0.offsets(thickness)

    cell_u = width / panel.edge_v0.count
    cell_v = height / panel.edge_u1.count
    cell_u_top = width / panel.edge_v1.count
    cell_v_left = height / panel.edge_u0.count

    points: list[Point2D] = []

    # Bottom-left corner, then left to right along the bottom edge.
    points.append((left[0], bottom[0]))
    for index in range(panel.edge_v0.count - 1):
        boundary = (index + 1) * cell_u
        points.append((boundary, bottom[index]))
        points.append((boundary, bottom[index + 1]))

    # Bottom-right corner, then upwards along the right edge.
    points.append((width - right[0], bottom[-1]))
    for index in range(panel.edge_u1.count - 1):
        boundary = (index + 1) * cell_v
        points.append((width - right[index], boundary))
        points.append((width - right[index + 1], boundary))

    # Top-right corner, then right to left along the top edge.
    points.append((width - right[-1], height - top[-1]))
    for index in reversed(range(panel.edge_v1.count - 1)):
        boundary = (index + 1) * cell_u_top
        points.append((boundary, height - top[index + 1]))
        points.append((boundary, height - top[index]))

    # Top-left corner, then downwards along the left edge back to the start.
    points.append((left[-1], height - top[0]))
    for index in reversed(range(panel.edge_u0.count - 1)):
        boundary = (index + 1) * cell_v_left
        points.append((left[index + 1], boundary))
        points.append((left[index], boundary))

    return _without_repeats(points)


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
    gap = max(layout_gap_min, thickness * 2)
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
