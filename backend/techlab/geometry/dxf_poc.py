"""TM23 supplement: MINIMAL experimental DXF R12 ASCII export.

FEASIBILITY PoC - what it is and is NOT:
  - IS: the smallest useful subset of the DXF R12 ASCII dialect -
    LINE, LWPOLYLINE (as POLYLINE), CIRCLE and TEXT entities with
    layer names - enough for a measurement sketch to round-trip into
    common CAD viewers (AutoCAD, LibreCAD, QCAD, DWG viewers).
  - IS NOT: a production format, a full DXF implementation (no
    blocks/dimensions/hatches), or part of the evidence protocol.
    It lives in techlab/ and stays experimental (task rule).
  - R12 (AC1009) is chosen deliberately: it is the last pure-ASCII
    dialect universally readable, with a flat ENTITIES-only file
    being legal.

Every entity carries the WorkCam layer naming convention
(WCM-MEASURE-<record_id>) so imports are traceable to their
measurement record.
"""

from __future__ import annotations

from techlab.geometry.measurement_format import MeasurementRecord


def records_to_dxf(records: list[MeasurementRecord],
                   layers_comment: str = "WorkCam measurement sketch"
                   ) -> str:
    """ASCII DXF R12 with one ENTITIES section: raw-point polylines
    per record + value labels. World XY plane; Z is carried on LINE
    endpoints (R12 is 3D-capable for LINE)."""
    if not records:
        raise ValueError("no records")
    parts: list[str] = ["0", "SECTION", "2", "ENTITIES"]

    def pair(code: int | str, value) -> None:
        parts.append(str(code))
        parts.append(str(value))

    for r in records:
        layer = f"WCM-MEASURE-{r.measurement_id}"
        # polyline through the raw points (R12 POLYLINE/VERTEX/SEQEND)
        if len(r.raw_points) >= 2:
            pair(0, "POLYLINE")
            pair(8, layer)
            pair(66, 1)             # vertices follow
            pair(70, 0)             # open polyline
            for p in r.raw_points:
                pair(0, "VERTEX")
                pair(8, layer)
                pair(10, f"{p[0]:.6f}")
                pair(20, f"{p[1]:.6f}")
                pair(30, f"{p[2]:.6f}")
            pair(0, "SEQEND")
            pair(8, layer)
        # single points become CIRCLE so they stay visible
        elif len(r.raw_points) == 1:
            p = r.raw_points[0]
            pair(0, "CIRCLE")
            pair(8, layer)
            pair(10, f"{p[0]:.6f}")
            pair(20, f"{p[1]:.6f}")
            pair(30, f"{p[2]:.6f}")
            pair(40, "0.01")
        # value label at the first point
        if r.raw_points:
            p = r.raw_points[0]
            pair(0, "TEXT")
            pair(8, layer)
            pair(10, f"{p[0] + 0.05:.6f}")
            pair(20, f"{p[1] + 0.05:.6f}")
            pair(30, "0.0")
            pair(40, "0.05")        # text height (drawing units)
            pair(1, f"{r.measurement_type.value}={r.value:g}"
                    f"{r.units.value} [{r.input_kind.value}]")
    parts += ["0", "ENDSEC", "0", "EOF"]
    # minimal header comment rides as group 999 (drawing comment)
    header = ["0", "SECTION", "2", "HEADER",
              "9", "$WORKCAM_NOTE", "1",
              f"{layers_comment} (experimental DXF R12 PoC)",
              "0", "ENDSEC"]
    return "\n".join(header + parts) + "\n"
