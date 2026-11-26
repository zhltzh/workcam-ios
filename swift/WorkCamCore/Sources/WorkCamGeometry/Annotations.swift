// TM63: annotation geometry engine - Swift port of
// backend/techlab/geometry/annotations.py.
// Rendering-AGNOSTIC: normalized coords, pure geometry, command-based
// undo/redo. UIKit/SwiftUI/CoreGraphics never imported here.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum AnnotationKind: String, Codable, Sendable, CaseIterable {
    case point
    case arrow
    case rectangle
    case ellipse
    case freehand
    case text
    case polygon
    case measurementLine = "measurement_line"
    case numberMarker = "number_marker"
}

public struct Annotation: Sendable, Equatable, Codable {
    public let annotationID: String
    public let kind: AnnotationKind
    public let points: [(u: Double, v: Double)]   // normalized 0..1
    public var colorHex: String
    public var strokeWidth: Double
    public var textContent: String?
    public var markerNumber: Int?
    public var zOrder: Int

    public enum AnnotationError: Error, Equatable {
        case emptyPoints
        case outsideFrame(u: Double, v: Double)
        case wrongArity(kind: AnnotationKind, got: Int)
        case missingText
    }

    public init(annotationID: String, kind: AnnotationKind,
                points: [(u: Double, v: Double)],
                colorHex: String = "#FF3B30", strokeWidth: Double = 4.0,
                textContent: String? = nil, markerNumber: Int? = nil,
                zOrder: Int = 0) throws {
        guard !points.isEmpty else { throw AnnotationError.emptyPoints }
        for p in points {
            guard p.u.isFinite, p.v.isFinite,
                  p.u >= 0, p.u <= 1, p.v >= 0, p.v <= 1 else {
                throw AnnotationError.outsideFrame(u: p.u, v: p.v)
            }
        }
        switch kind {
        case .arrow, .measurementLine, .rectangle:
            guard points.count == 2 else {
                throw AnnotationError.wrongArity(kind: kind,
                                                  got: points.count)
            }
        case .freehand, .polygon:
            guard points.count >= 3 else {
                throw AnnotationError.wrongArity(kind: kind,
                                                  got: points.count)
            }
        case .text:
            guard textContent != nil, !textContent!.isEmpty else {
                throw AnnotationError.missingText
            }
        default:
            break
        }
        self.annotationID = annotationID
        self.kind = kind
        self.points = points
        self.colorHex = colorHex
        self.strokeWidth = strokeWidth
        self.textContent = textContent
        self.markerNumber = markerNumber
        self.zOrder = zOrder
    }

    // Codable with labeled tuples: encode as [[u, v]] arrays.
    private enum CodingKeys: String, CodingKey {
        case annotationID = "annotation_id"
        case kind, colorHex = "color_hex"
        case strokeWidth = "stroke_width"
        case textContent = "text_content"
        case markerNumber = "marker_number"
        case zOrder = "z_order"
        case points
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(
            keyedBy: CodingKeys.self)
        let raw = try c.decode([[Double]].self, forKey: .points)
        let pts = raw.map { ($0[0], $0[1]) }
        try self.init(
            annotationID: try c.decode(String.self,
                                       forKey: .annotationID),
            kind: try c.decode(AnnotationKind.self, forKey: .kind),
            points: pts,
            colorHex: try c.decode(String.self, forKey: .colorHex),
            strokeWidth: try c.decode(Double.self,
                                      forKey: .strokeWidth),
            textContent: try c.decodeIfPresent(String.self,
                                                forKey: .textContent),
            markerNumber: try c.decodeIfPresent(Int.self,
                                                forKey: .markerNumber),
            zOrder: try c.decode(Int.self, forKey: .zOrder))
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(annotationID, forKey: .annotationID)
        try c.encode(kind, forKey: .kind)
        try c.encode(points.map { [$0.u, $0.v] }, forKey: .points)
        try c.encode(colorHex, forKey: .colorHex)
        try c.encode(strokeWidth, forKey: .strokeWidth)
        try c.encodeIfPresent(textContent, forKey: .textContent)
        try c.encodeIfPresent(markerNumber, forKey: .markerNumber)
        try c.encode(zOrder, forKey: .zOrder)
    }

    public var bbox: (minU: Double, minV: Double,
                      maxU: Double, maxV: Double) {
        let us = points.map(\.u), vs = points.map(\.v)
        return (us.min()!, vs.min()!, us.max()!, vs.max()!)
    }

    // MARK: - display-pipeline transform (TM15 orientation + crop)

    public func transformed(orientation: PixelOrientation,
                            crop: CropRect? = nil) throws -> Annotation {
        let W = 1000, H = 1000
        let px = points.map { ($0.u * Double(W - 1),
                               $0.v * Double(H - 1)) }
        var oriented = px.map {
            orientation.uprightPixel(u: $0.u, v: $0.v,
                                    width: W, height: H)
        }
        if let crop {
            let mapped: [(Double, Double)] = try oriented.map { p in
                let nu = (p.u / Double(W - 1) - crop.x) / crop.w
                let nv = (p.v / Double(H - 1) - crop.y) / crop.h
                guard nu >= 0, nu <= 1, nv >= 0, nv <= 1 else {
                    throw AnnotationError.outsideFrame(u: nu, v: nv)
                }
                return (nu, nv)
            }
            oriented = mapped
        } else {
            let dims = orientation.uprightDimensions(width: W,
                                                      height: H)
            oriented = oriented.map {
                ($0.u / Double(dims.width - 1),
                 $0.v / Double(dims.height - 1))
            }
        }
        return try Annotation(
            annotationID: annotationID, kind: kind, points: oriented,
            colorHex: colorHex, strokeWidth: strokeWidth,
            textContent: textContent, markerNumber: markerNumber,
            zOrder: zOrder)
    }

    // MARK: - hit testing (pixel-slop affordance)

    public func hitTest(u: Double, v: Double, slopPx: Double = 12.0,
                        viewWidth: Int = 1000,
                        viewHeight: Int = 1000) -> Bool {
        let su = slopPx / Double(viewWidth)
        let sv = slopPx / Double(viewHeight)
        switch kind {
        case .point, .text, .numberMarker:
            let p = points[0]
            return abs(u - p.u) <= su && abs(v - p.v) <= sv
        case .arrow, .measurementLine, .freehand:
            if points.count == 1 {
                return abs(u - points[0].u) <= su
                    && abs(v - points[0].v) <= sv
            }
            let segs = zip(points, points.dropFirst())
            for (a, b) in segs {
                if Self.distToSegment(px: u, py: v,
                                      a: a, b: b) <= max(su, sv) {
                    return true
                }
            }
            return false
        case .rectangle:
            let (p1, p2) = (points[0], points[1])
            let loU = min(p1.u, p2.u), hiU = max(p1.u, p2.u)
            let loV = min(p1.v, p2.v), hiV = max(p1.v, p2.v)
            return (loU - su...hiU + su).contains(u)
                && (loV - sv...hiV + sv).contains(v)
        case .ellipse:
            let (p1, p2) = (points[0], points[1])
            let cx = (p1.u + p2.u) / 2, cy = (p1.v + p2.v) / 2
            let ru = abs(p2.u - p1.u) / 2 + su
            let rv = abs(p2.v - p1.v) / 2 + sv
            guard ru > 0, rv > 0 else { return false }
            let eu = (u - cx) / ru, ev = (v - cy) / rv
            return eu * eu + ev * ev <= 1
        case .polygon:
            if Self.pointInPolygon(u, v, points) { return true }
            let ring = points + [points[0]]
            for (a, b) in zip(ring, ring.dropFirst()) {
                if Self.distToSegment(px: u, py: v, a: a, b: b)
                        <= max(su, sv) {
                    return true
                }
            }
            return false
        }
    }

    static func distToSegment(px: Double, py: Double,
                              a: (u: Double, v: Double),
                              b: (u: Double, v: Double)) -> Double {
        let abx = b.u - a.u, aby = b.v - a.v
        let apx = px - a.u, apy = py - a.v
        let ab2 = abx * abx + aby * aby
        if ab2 < 1e-18 { return (apx * apx + apy * apy).squareRoot() }
        let t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
        return ((apx - t * abx) * (apx - t * abx)
                + (apy - t * aby) * (apy - t * aby)).squareRoot()
    }

    static func pointInPolygon(_ px: Double, _ py: Double,
                              _ pts: [(u: Double, v: Double)]) -> Bool {
        var inside = false
        var j = pts.count - 1
        for i in 0..<pts.count {
            let pi = pts[i], pj = pts[j]
            if (pi.v > py) != (pj.v > py) {
                let xAt = (pj.u - pi.u) * (py - pi.v)
                    / (pj.v - pi.v) + pi.u
                if px < xAt { inside.toggle() }
            }
            j = i
        }
        return inside
    }
}

// MARK: - document with command-based undo/redo (soft delete)

public final class AnnotationDocument: @unchecked Sendable {
    private var items: [String: Annotation] = [:]
    private var deleted: [String: Annotation] = [:]
    private var undoStack: [(op: String, annotation: Annotation,
                              previous: Annotation?)] = []
    private var redoStack: [(op: String, annotation: Annotation,
                             previous: Annotation?)] = []

    public init() {}

    public func visible() -> [Annotation] {
        items.values.sorted { $0.zOrder < $1.zOrder }
    }

    public func get(_ id: String) -> Annotation? { items[id] }

    public func hitTopmost(u: Double, v: Double,
                           slopPx: Double = 12.0,
                           viewWidth: Int = 1000,
                           viewHeight: Int = 1000) -> Annotation? {
        for a in visible().reversed() {
            if a.hitTest(u: u, v: v, slopPx: slopPx,
                         viewWidth: viewWidth, viewHeight: viewHeight) {
                return a
            }
        }
        return nil
    }

    public func add(_ a: Annotation) throws {
        guard items[a.annotationID] == nil else {
            throw Annotation.AnnotationError.wrongArity(
                kind: a.kind, got: -1)     // id-collision proxy error
        }
        apply(("add", a, nil))
    }

    public func update(_ newVersion: Annotation) throws {
        guard let old = items[newVersion.annotationID] else {
            throw Annotation.AnnotationError.missingText
        }
        apply(("update", newVersion, old))
    }

    public func delete(_ id: String) throws {
        guard let a = items[id] else {
            throw Annotation.AnnotationError.missingText
        }
        apply(("delete", a, a))
    }

    @discardableResult
    public func undo() -> Bool {
        guard let cmd = undoStack.popLast() else { return false }
        revert(cmd)
        redoStack.append(cmd)
        return true
    }

    @discardableResult
    public func redo() -> Bool {
        guard let cmd = redoStack.popLast() else { return false }
        apply(cmd)
        return true
    }

    public var canUndo: Bool { !undoStack.isEmpty }
    public var canRedo: Bool { !redoStack.isEmpty }

    private func apply(_ cmd: (op: String, annotation: Annotation,
                              previous: Annotation?)) {
        switch cmd.op {
        case "add": items[cmd.annotation.annotationID] = cmd.annotation
        case "update": items[cmd.annotation.annotationID] = cmd.annotation
        case "delete":
            deleted[cmd.annotation.annotationID] = cmd.annotation
            items.removeValue(forKey: cmd.annotation.annotationID)
        default: break
        }
        undoStack.append(cmd)
        redoStack.removeAll()      // new action kills the redo branch
    }

    private func revert(_ cmd: (op: String, annotation: Annotation,
                               previous: Annotation?)) {
        switch cmd.op {
        case "add":
            items.removeValue(forKey: cmd.annotation.annotationID)
        case "update":
            items[cmd.annotation.annotationID] = cmd.previous
        case "delete":
            items[cmd.annotation.annotationID] =
                deleted.removeValue(
                    forKey: cmd.annotation.annotationID)
        default: break
        }
    }
}
