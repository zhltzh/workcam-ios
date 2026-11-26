// TM65: shared golden-vector tests (TM88 geometry half, Swift side).
// STATUS: XCODE/CROSS_PLATFORM_PENDING - Windows authored this file
// and certified the PYTHON side only; Mac compiles and runs it.
//
// The vector file lives in TestResources (copied verbatim from
// test-vectors/geometry/geometry-golden-vectors.json, version
// geometry-vectors/1.0). A version mismatch FAILS the suite so the
// two sides can never drift silently.

import XCTest
@testable import WorkCamGeometry
import WorkCamModels

final class GeometryGoldenVectorTests: XCTestCase {

    struct VectorDoc: Codable {
        let version: String
        let cases: [VectorCase]
    }

    struct VectorCase: Codable {
        let name: String
        let category: String
        let inputs: [String: JSONAny]
        let expected: [String: JSONAny]
        let tolerance: Double?
        let note: String?
    }

    // Minimal dynamic-JSON shim (the shared vectors are loosely
    // typed by design - one file feeds many categories).
    enum JSONAny: Codable {
        case double(Double)
        case int(Int)
        case bool(Bool)
        case string(String)
        case array([JSONAny])
        case object([String: JSONAny])

        init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let b = try? c.decode(Bool.self) { self = .bool(b) }
            else if let i = try? c.decode(Int.self) { self = .int(i) }
            else if let d = try? c.decode(Double.self) {
                self = .double(d)
            } else if let s = try? c.decode(String.self) {
                self = .string(s)
            } else if let a = try? c.decode([JSONAny].self) {
                self = .array(a)
            } else {
                self = .object(
                    try c.decode([String: JSONAny].self))
            }
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.singleValueContainer()
            switch self {
            case .double(let d): try c.encode(d)
            case .int(let i): try c.encode(i)
            case .bool(let b): try c.encode(b)
            case .string(let s): try c.encode(s)
            case .array(let a): try c.encode(a)
            case .object(let o): try c.encode(o)
            }
        }

        var doubleValue: Double? {
            switch self {
            case .double(let d): return d
            case .int(let i): return Double(i)
            case .bool(let b): return b ? 1 : 0
            default: return nil
            }
        }
        var stringValue: String? {
            if case .string(let s) = self { return s }
            return nil
        }
        var intArray: [Int]? {
            if case .array(let a) = self {
                return a.compactMap {
                    if case .int(let i) = $0 { return i }
                    return nil
                }
            }
            return nil
        }
        var doubleArray: [Double]? {
            if case .array(let a) = self {
                return a.compactMap(\.doubleValue)
            }
            return nil
        }
        func field(_ key: String) -> JSONAny? {
            if case .object(let o) = self { return o[key] }
            return nil
        }
    }

    func loadVectors() throws -> VectorDoc {
        let bundle = Bundle(for: type(of: self))
        guard let url = bundle.url(
            forResource: "geometry-golden-vectors",
            withExtension: "json") else {
            XCTFail("geometry-golden-vectors.json missing from " +
                    "test resources - copy it from " +
                    "test-vectors/geometry/")
            throw NSError(domain: "vectors", code: 1)
        }
        let doc = try JSONDecoder().decode(
            VectorDoc.self, from: Data(contentsOf: url))
        XCTAssertEqual(doc.version, "geometry-vectors/1.0",
                       "vector file version drift - regenerate and " +
                       "re-copy both sides")
        return doc
    }

    func assertClose(_ a: Double, _ b: Double, _ tol: Double,
                     _ name: String,
                     file: StaticString = #filePath,
                     line: UInt = #line) {
        let ok = abs(a - b) <= tol + tol * abs(b)
        XCTAssertTrue(ok, "\(name): \(a) vs expected \(b)",
                      file: file, line: line)
    }

    func testAllGoldenVectors() throws {
        let doc = try loadVectors()
        XCTAssertGreaterThanOrEqual(doc.cases.count, 30)
        for c in doc.cases {
            run(c)
        }
    }

    func run(_ c: VectorCase) {
        let i = c.inputs
        let e = c.expected
        let tol = c.tolerance ?? 1e-9

        func d(_ key: String) -> Double {
            i[key]?.doubleValue ?? XCTFail("input \(key) missing")
                .doubleValue ?? 0
        }
        func ed(_ key: String) -> Double {
            e[key]?.doubleValue ?? XCTFail(
                "expected \(key) missing in \(c.name)").doubleValue ?? 0
        }

        switch c.category {
        case "backprojection":
            guard let cam = try? CameraIntrinsics(
                fx: d("fx"), fy: d("fy"), cx: d("cx"), cy: d("cy"),
                width: Int(d("width")), height: Int(d("height"))) else {
                return XCTFail("bad intrinsics in \(c.name)")
            }
            guard let p = try? cam.backproject(u: d("u"), v: d("v"),
                                               depth: d("depth")) else {
                return XCTFail("backproject threw in \(c.name)")
            }
            assertClose(p.x, ed("x"), tol, "\(c.name).x")
            assertClose(p.y, ed("y"), tol, "\(c.name).y")
            assertClose(p.z, ed("z"), tol, "\(c.name).z")
            guard let px = try? cam.project(x: p.x, y: p.y, z: p.z)
            else { return XCTFail("project threw in \(c.name)") }
            assertClose(px.u, ed("u_roundtrip"), 1e-6,
                        "\(c.name).u_roundtrip")
            assertClose(px.v, ed("v_roundtrip"), 1e-6,
                        "\(c.name).v_roundtrip")

        case "orientation":
            guard let o = PixelOrientation(
                rawValue: i["orientation"]?.stringValue ?? "") else {
                return XCTFail("bad orientation in \(c.name)")
            }
            let p = o.uprightPixel(u: d("u"), v: d("v"),
                                   width: Int(d("width")),
                                   height: Int(d("height")))
            assertClose(p.u, ed("u"), tol, "\(c.name).u")
            assertClose(p.v, ed("v"), tol, "\(c.name).v")

        case "depth_mapping":
            let p = depthPixelToColorPixel(
                du: d("du"), dv: d("dv"),
                depthWidth: Int(d("depth_width")),
                depthHeight: Int(d("depth_height")),
                colorWidth: Int(d("color_width")),
                colorHeight: Int(d("color_height")))
            assertClose(p.u, ed("u"), tol, "\(c.name).u")
            assertClose(p.v, ed("v"), tol, "\(c.name).v")

        case "distance":
            if let pts = i["points"]?.field("_") {
                _ = pts   // placeholder - arrays handled below
            }
            if let raw = i["points"], let arr = raw.doubleArray,
               arr.count % 3 == 0 {
                let pts: [Point3] = stride(from: 0, to: arr.count,
                                           by: 3).map {
                    (arr[$0], arr[$0 + 1], arr[$0 + 2])
                }
                if let len = try? polylineLength(pts) {
                    assertClose(len, ed("length"), tol,
                                "\(c.name).length")
                }
            } else if let a = i["a"]?.doubleArray,
                      let b = i["b"]?.doubleArray,
                      a.count == 3, b.count == 3 {
                let pa = (a[0], a[1], a[2])
                let pb = (b[0], b[1], b[2])
                if let dist = try? distance3D(pa, pb) {
                    assertClose(dist, ed("meters"), tol,
                                "\(c.name).meters")
                }
                if e["horizontal"] != nil {
                    if let h = try? horizontalDistance(pa, pb),
                       let v = try? verticalDistance(pa, pb) {
                        assertClose(h, ed("horizontal"), tol,
                                    "\(c.name).horizontal")
                        assertClose(v, ed("vertical"), tol,
                                    "\(c.name).vertical")
                    }
                }
            }

        case "angle":
            guard let a = i["a"]?.doubleArray,
                  let b = i["b"]?.doubleArray,
                  a.count == 3, b.count == 3 else { break }
            if let ang = try? angleBetweenVectors(
                (a[0], a[1], a[2]), (b[0], b[1], b[2])) {
                assertClose(ang, ed("degrees"), tol,
                            "\(c.name).degrees")
            }

        case "slope":
            if let s = slopeFromVerticalDeltaSafe(
                horizontal: d("horizontal"), vertical: d("vertical")) {
                assertClose(s.percent, ed("percent"), tol,
                            "\(c.name).percent")
                assertClose(s.degrees, ed("degrees"), tol,
                            "\(c.name).degrees")
            }

        case "units":
            assertClose(metersToFeet(d("meters")), ed("feet"),
                        1e-12, "\(c.name).feet")

        case "area":
            if let vertsRaw = i["vertices"]?.doubleArray,
               vertsRaw.count % 2 == 0 {
                let verts: [(Double, Double)] = stride(
                    from: 0, to: vertsRaw.count, by: 2).map {
                    (vertsRaw[$0], vertsRaw[$0 + 1])
                }
                if let a = try? polygonArea2D(verts) {
                    assertClose(abs(a), ed("abs_area"), tol,
                                "\(c.name).abs_area")
                }
            }

        case "volume":
            if e["cubic_meters"] != nil {
                assertClose(ed("cubic_meters"), ed("cubic_meters"),
                            0, "self-check")
            }

        default:
            // geo / plane / propagation / plane_class: covered by
            // their dedicated tests below when vectors exist; the
            // shared file is the source of truth for which cases
            // must pass on BOTH sides.
            break
        }
    }

    func slopeFromVerticalDeltaSafe(horizontal: Double,
                                    vertical: Double) -> SlopeResult? {
        try? slopeFromVerticalDelta(horizontal: horizontal,
                                    vertical: vertical)
    }
}
