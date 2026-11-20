import Foundation

/// Optical image coordinates: +x right, +y down. AR camera: +y up, -z forward.
enum DepthGeometry {
    static func cameraPoint(x: Int, y: Int, width: Int, height: Int,
                            imageWidth: Double, imageHeight: Double,
                            fx: Float, fy: Float, cx: Float, cy: Float,
                            depth: Float) -> SIMD3<Float>? {
        guard width > 0, height > 0, x >= 0, x < width, y >= 0, y < height,
              imageWidth.isFinite, imageHeight.isFinite, imageWidth > 0, imageHeight > 0,
              fx.isFinite, fy.isFinite, fx > 0, fy > 0,
              cx.isFinite, cy.isFinite, depth.isFinite, depth > 0, depth <= 10 else { return nil }
        let u = Float((Double(x) + 0.5) / Double(width) * imageWidth)
        let v = Float((Double(y) + 0.5) / Double(height) * imageHeight)
        let result = SIMD3<Float>((u - cx) * depth / fx, -(v - cy) * depth / fy, -depth)
        return result.x.isFinite && result.y.isFinite ? result : nil
    }
}
