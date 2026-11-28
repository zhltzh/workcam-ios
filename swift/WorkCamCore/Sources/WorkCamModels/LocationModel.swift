// TM-04 D07: engineering location hierarchy - Swift mirror of
// backend/techlab/business/location_model.py. Segment-aligned
// prefix matching, explicitness refusals, path-safety rules.
// REAL Windows compile + unit tests; the parity suite pins the
// two languages together.

import Foundation

public enum LocationModelError: Error, Equatable {
    case emptySegment(level: String)
    case unsafeSegment(String)
    case segmentTooLong(level: String)
    case emptyPath
    case tooDeep(got: Int, max: Int)
}

public struct LocationPath: Sendable, Equatable, Hashable {
    public static let maxLevels = 4
    public static let levelNames = ["area", "floor", "room", "spot"]

    public let segments: [String]      // normalized display casing
    let indexKey: String               // casefolded join

    public init(_ segments: String...) throws {
        let built = try Self.build(segments)
        self.segments = built.segments
        self.indexKey = built.indexKey
    }

    public init(_ segments: [String]) throws {
        let built = try Self.build(segments)
        self.segments = built.segments
        self.indexKey = built.indexKey
    }

    public static func parse(_ joined: String) throws -> LocationPath {
        try LocationPath(joined.split(separator: "/",
                                       omittingEmptySubsequences: false)
            .map(String.init))
    }

    private static func build(_ raw: [String])
            throws -> (segments: [String], indexKey: String) {
        guard !raw.isEmpty else {
            throw LocationModelError.emptyPath
        }
        guard raw.count <= maxLevels else {
            throw LocationModelError.tooDeep(got: raw.count,
                                            max: maxLevels)
        }
        var normalized: [String] = []
        for (i, seg) in raw.enumerated() {
            let collapsed = seg.split(whereSeparator: \.isWhitespace)
                .joined(separator: " ")
            guard !collapsed.isEmpty else {
                throw LocationModelError.emptySegment(
                    level: levelNames[i])
            }
            // path separators and control characters never enter a
            // location (traversal strings are refused by name)
            for ch in collapsed.unicodeScalars
            where ch == "/" || ch == "\\" || (ch.value < 0x20)
                || ch.value == 0x7f {
                throw LocationModelError.unsafeSegment(collapsed)
            }
            guard collapsed.count <= 64 else {
                throw LocationModelError.segmentTooLong(
                    level: levelNames[i])
            }
            normalized.append(collapsed)
        }
        let key = normalized.map { $0.lowercased() }
            .joined(separator: "/")
        return (normalized, key)
    }

    public var area: String { segments[0] }
    public var floor: String? { segments.count > 1 ? segments[1] : nil }
    public var room: String? { segments.count > 2 ? segments[2] : nil }
    public var depth: Int { segments.count }
    public var display: String { segments.joined(separator: "/") }

    public static func == (lhs: LocationPath, rhs: LocationPath)
            -> Bool { lhs.indexKey == rhs.indexKey }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(indexKey)
    }

    /// Segment-aligned, case-insensitive prefix match.
    public func isDescendant(of ancestor: LocationPath) -> Bool {
        guard ancestor.depth <= depth else { return false }
        return zip(segments, ancestor.segments).allSatisfy {
            $0.caseInsensitiveCompare($1) == .orderedSame
        }
    }
}

public enum LocationQuery {

    /// Index predicate: nil query matches everything; a query
    /// matches the candidate itself and every descendant.
    public static func matches(_ query: LocationPath?,
                               candidate: LocationPath) -> Bool {
        guard let query else { return true }
        if query == candidate { return true }
        return candidate.isDescendant(of: query)
    }

    /// Report grouping by casefolded area, insertion order kept.
    public static func groupByArea(
        _ paths: [LocationPath]) -> [(area: String,
                                      paths: [LocationPath])] {
        var order: [String] = []
        var buckets: [String: [LocationPath]] = [:]
        for p in paths {
            let key = p.area.lowercased()
            if buckets[key] == nil { order.append(key) }
            buckets[key, default: []].append(p)
        }
        return order.map { ($0, buckets[$0]!) }
    }
}
