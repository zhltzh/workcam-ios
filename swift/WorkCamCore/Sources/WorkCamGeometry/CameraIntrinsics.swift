// TM61: camera intrinsics, back-projection, orientation & depth-map
// mapping - Swift port of backend/techlab/geometry/camera.py (TM15).
// Ported line-by-line against the Python reference; the shared golden
// vectors (TM88) pin the numeric contract on BOTH sides.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

/// Thrown by every geometry entry point on degenerate input. The
/// Python side raises ValueError with the same message - the messages
/// are part of the cross-language contract (tests compare prefixes).
public enum GeometryError: Error, Equatable, Sendable {
    case nonFinite(String)
    case invalidParameter(String)
}

public struct CameraIntrinsics: Sendable, Equatable {
    public let fx: Double
    public let fy: Double
    public let cx: Double
    public let cy: Double
    public let width: Int
    public let height: Int

    public init(fx: Double, fy: Double, cx: Double, cy: Double,
                width: Int, height: Int) throws {
        guard fx.isFinite, fy.isFinite, cx.isFinite, cy.isFinite else {
            throw GeometryError.nonFinite("intrinsics")
        }
        guard fx > 0, fy > 0 else {
            throw GeometryError.invalidParameter(
                "focal lengths must be positive")
        }
        guard width > 0, height > 0 else {
            throw GeometryError.invalidParameter(
                "image dimensions must be positive")
        }
        self.fx = fx; self.fy = fy; self.cx = cx; self.cy = cy
        self.width = width; self.height = height
    }

    /// Pixel -> normalized device coordinates (Metal convention).
    public func pixelToNormalized(
        u: Double, v: Double) -> (x: Double, y: Double) {
        let x = (2.0 * u - Double(width)) / Double(width)
        let y = 1.0 - (2.0 * v) / Double(height)
        return (x, y)
    }

    /// Pixel + optical-axis depth -> camera-space point.
    /// Same pinhole contract as the Python reference: the depth is
    /// the z-distance (ARKit sceneDepth), not the euclidean ray
    /// length. Camera looks down -z, image v grows DOWN, camera y
    /// grows UP.
    public func backproject(u: Double, v: Double,
                            depth: Double) throws
            -> (x: Double, y: Double, z: Double) {
        guard u.isFinite, v.isFinite, depth.isFinite else {
            throw GeometryError.nonFinite("backproject input")
        }
        guard depth > 0 else {
            throw GeometryError.invalidParameter(
                "depth must be positive")
        }
        let x = (u - cx) / fx * depth
        let y = (cy - v) / fy * depth
        return (x, y, -depth)
    }

    /// Camera-space point -> pixel (inverse of backproject).
    public func project(x: Double, y: Double,
                        z: Double) throws -> (u: Double, v: Double) {
        guard x.isFinite, y.isFinite, z.isFinite else {
            throw GeometryError.nonFinite("project input")
        }
        guard z < -1e-12 else {
            throw GeometryError.invalidParameter(
                "point is at/behind the camera plane")
        }
        let u = cx + fx * x / -z
        let v = cy - fy * y / -z
        return (u, v)
    }
}

/// EXIF/AVCapture orientation mapping (top-left raw buffer pixels ->
/// upright display pixels). The EXIF numbers are in the doc comments;
/// the TM88 orientation vectors pin the corner mappings.
public enum PixelOrientation: String, Sendable, CaseIterable {
    case up                     // EXIF 1: identity
    case down                   // EXIF 3: rotate 180
    case right                  // EXIF 6: rotate 90 CCW
    case left                   // EXIF 8: rotate 90 CW
    case portrait               // AVCapture back camera: == right
    case portraitUpsideDown     // AVCapture: == left
    case landscapeLeft          // AVCapture: == up
    case landscapeRight         // AVCapture: == down

    public func uprightPixel(u: Double, v: Double, width: Int,
                             height: Int) -> (u: Double, v: Double) {
        let w = Double(width - 1)
        let h = Double(height - 1)
        switch canonical {
        case .up: return (u, v)
        case .down: return (w - u, h - v)
        case .right: return (v, w - u)
        case .left: return (h - v, u)
        }
    }

    public func uprightDimensions(width: Int, height: Int)
            -> (width: Int, height: Int) {
        switch canonical {
        case .up, .down: return (width, height)
        case .right, .left: return (height, width)
        }
    }

    var canonical: PixelOrientation {
        switch self {
        case .up, .landscapeLeft: return .up
        case .down, .landscapeRight: return .down
        case .right, .portrait: return .right
        case .left, .portraitUpsideDown: return .left
        }
    }
}

/// Depth-map -> color-photo pixel mapping (ARKit sceneDepth is lower
/// resolution and center-aligned with the photo frame).
public func depthPixelToColorPixel(
    du: Double, dv: Double,
    depthWidth: Int, depthHeight: Int,
    colorWidth: Int, colorHeight: Int)
        -> (u: Double, v: Double) {
    let u = (du + 0.5) * Double(colorWidth) / Double(depthWidth) - 0.5
    let v = (dv + 0.5) * Double(colorHeight) / Double(depthHeight) - 0.5
    return (u, v)
}

/// Nearest-neighbor depth lookup. Out-of-range and non-positive
/// values are NaN - callers MUST treat NaN as invalid (no clamping,
/// no fabricated measurements).
public func sampleDepthNearest(_ depth: [[Double]], du: Double,
                               dv: Double) -> Double {
    guard !depth.isEmpty, !depth[0].isEmpty else { return .nan }
    let h = depth.count
    let w = depth[0].count
    let i = Int((dv).rounded())
    let j = Int((du).rounded())
    guard i >= 0, i < h, j >= 0, j < w else { return .nan }
    let val = depth[i][j]
    if !val.isFinite || val <= 0 { return .nan }
    return val
}

/// Normalized crop rect (0..1, top-left origin) with round-trip
/// pixel mapping between the original and cropped image.
public struct CropRect: Sendable {
    public let x: Double, y: Double, w: Double, h: Double

    public init(x: Double, y: Double, w: Double, h: Double) throws {
        guard [x, y, w, h].allSatisfy({ $0.isFinite }) else {
            throw GeometryError.nonFinite("crop rect")
        }
        guard w > 0, h > 0 else {
            throw GeometryError.invalidParameter(
                "crop size must be positive")
        }
        guard x >= 0, y >= 0, x + w <= 1 + 1e-12,
              y + h <= 1 + 1e-12 else {
            throw GeometryError.invalidParameter(
                "crop rect out of the normalized frame")
        }
        self.x = x; self.y = y; self.w = w; self.h = h
    }

    public func originalPixelToCropped(
        u: Double, v: Double, width: Int, height: Int) throws
            -> (u: Double, v: Double) {
        let cu = (u / Double(width) - x) / w
        let cv = (v / Double(height) - y) / h
        guard cu >= 0, cu <= 1, cv >= 0, cv <= 1 else {
            throw GeometryError.invalidParameter(
                "pixel lies outside the crop")
        }
        let outW = max(1, Int((w * Double(width)).rounded()))
        let outH = max(1, Int((h * Double(height)).rounded()))
        return (cu * Double(outW - 1), cv * Double(outH - 1))
    }

    public func croppedPixelToOriginal(
        cu: Double, cv: Double, width: Int, height: Int)
            -> (u: Double, v: Double) {
        let outW = max(1, Int((w * Double(width)).rounded()))
        let outH = max(1, Int((h * Double(height)).rounded()))
        let u = (cu / Double(outW - 1) * w + x) * Double(width)
        let v = (cv / Double(outH - 1) * h + y) * Double(height)
        return (u, v)
    }
}

/// Center-based resolution scaling (same contract as the depth map).
public func scalePixel(u: Double, v: Double, srcW: Int, srcH: Int,
                       dstW: Int, dstH: Int)
        -> (u: Double, v: Double) {
    let u2 = (u + 0.5) * Double(dstW) / Double(srcW) - 0.5
    let v2 = (v + 0.5) * Double(dstH) / Double(srcH) - 0.5
    return (u2, v2)
}
