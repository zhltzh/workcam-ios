// TM64: watermark LAYOUT engine - Swift port of
// backend/techlab/geometry/watermark_layout.py.
// Rendering-free: fonts are MEASURED by the caller (CoreText on Mac)
// and fed in; the placement math is pure and cross-identical.
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).

import Foundation

public enum WatermarkLayoutError: Error, Equatable, Sendable {
    case named(String)
}

public struct Rect: Sendable, Equatable {
    public let x: Double, y: Double, w: Double, h: Double
    public init(_ x: Double, _ y: Double, _ w: Double, _ h: Double) {
        self.x = x; self.y = y; self.w = w; self.h = h
    }
}

public struct MeasuredLine: Sendable, Equatable {
    public let text: String
    public let bold: Bool
    public let widthPx: Double
    public let heightPx: Double

    public init(text: String, bold: Bool, widthPx: Double,
                heightPx: Double) {
        self.text = text
        self.bold = bold
        self.widthPx = widthPx
        self.heightPx = heightPx
    }
}

public enum WatermarkCorner: String, Sendable, CaseIterable {
    case bottomLeft = "bottom_left"
    case bottomRight = "bottom_right"
    case topLeft = "top_left"
    case topRight = "top_right"
}

public struct LayoutResult: Sendable, Equatable {
    public let blockRect: Rect
    public let logoRect: Rect?
    public let lineRects: [Rect]
    public let textOrigin: (x: Double, y: Double)
    public let order: [String]
    public let safeMarginPx: Double
    public let corner: WatermarkCorner

    public static func == (l: LayoutResult, r: LayoutResult) -> Bool {
        l.blockRect == r.blockRect && l.logoRect == r.logoRect
            && l.lineRects == r.lineRects
            && l.textOrigin.x == r.textOrigin.x
            && l.textOrigin.y == r.textOrigin.y
            && l.order == r.order
            && l.safeMarginPx == r.safeMarginPx
            && l.corner == r.corner
    }
}

public func computeLayout(
    imageSize: (width: Int, height: Int),
    corner: WatermarkCorner,
    marginPx: Double,
    lineGapPx: Double,
    lines: [MeasuredLine],
    logoSize: (width: Double, height: Double)? = nil,
    maxBlockHeightRatio: Double = 0.55,
    maxTextWidthRatio: Double = 0.62,
    clampToImage: Bool = true) throws -> LayoutResult {
    let width = Double(imageSize.width)
    let height = Double(imageSize.height)
    guard width > 0, height > 0 else {
        throw WatermarkLayoutError.named(
            "image dimensions must be positive")
    }
    guard marginPx >= 0, lineGapPx >= 0 else {
        throw WatermarkLayoutError.named("margins must be >= 0")
    }
    guard maxBlockHeightRatio > 0, maxBlockHeightRatio <= 1,
          maxTextWidthRatio > 0, maxTextWidthRatio <= 1 else {
        throw WatermarkLayoutError.named("ratio out of range")
    }
    for ln in lines {
        guard ln.widthPx.isFinite, ln.heightPx.isFinite,
              ln.widthPx >= 0, ln.heightPx >= 0 else {
            throw WatermarkLayoutError.named(
                "measured line sizes must be finite and >= 0")
        }
    }
    guard !lines.isEmpty || logoSize != nil else {
        throw WatermarkLayoutError.named(
            "nothing to render: no lines and no logo - enable at " +
            "least one field (W011 all-toggles-off rule)")
    }
    var logoW = 0.0, logoH = 0.0
    if let l = logoSize {
        guard l.width > 0, l.height > 0 else {
            throw WatermarkLayoutError.named(
                "logo size must be positive")
        }
        logoW = l.width; logoH = l.height
    }

    // text width budget (W011 semantics)
    var maxTextWidth = width * maxTextWidthRatio
    if logoSize != nil {
        maxTextWidth = min(maxTextWidth, width - logoW - 3 * marginPx)
    }
    var textW = lines.map(\.widthPx).max() ?? 0
    if logoSize != nil { textW = max(textW, logoW) }
    if textW > maxTextWidth {
        throw WatermarkLayoutError.named(
            String(format: "widest measured line %.0fpx exceeds " +
                    "the %.0fpx budget - wrap the text before " +
                    "laying out (caller's wrap step)",
                    textW, maxTextWidth))
    }
    let blockW = min(width - 2 * marginPx, textW + 2 * marginPx)

    // block height
    let logoGap = logoSize != nil ? lineGapPx : 0
    let blockH = marginPx * 2 + logoH + logoGap
        + lines.reduce(0) { $0 + $1.heightPx + lineGapPx }
    if blockH > height * maxBlockHeightRatio {
        throw WatermarkLayoutError.named(
            String(format: "block height %.0fpx exceeds the budget " +
                    "- reduce lines or shrink the font (never a " +
                    "silent clip)", blockH))
    }

    // corner placement
    var px: Double, py: Double
    switch corner {
    case .bottomLeft: (px, py) = (marginPx, height - blockH - marginPx)
    case .bottomRight:
        (px, py) = (width - blockW - marginPx,
                    height - blockH - marginPx)
    case .topLeft: (px, py) = (marginPx, marginPx)
    case .topRight: (px, py) = (width - blockW - marginPx, marginPx)
    }
    if clampToImage {
        px = max(0, min(px, width - blockW))
        py = max(0, min(py, height - blockH))
    }

    // inner rects
    var logoRect: Rect? = nil
    var cx = px + marginPx
    var cy = py + marginPx
    if logoSize != nil {
        logoRect = Rect(cx, cy, logoW, logoH)
        cy += logoH + lineGapPx
    }
    let textOrigin = (cx, cy)
    var lineRects: [Rect] = []
    for ln in lines {
        lineRects.append(Rect(cx, cy, ln.widthPx, ln.heightPx))
        cy += ln.heightPx + lineGapPx
    }
    return LayoutResult(
        blockRect: Rect(px, py, blockW, blockH),
        logoRect: logoRect, lineRects: lineRects,
        textOrigin: textOrigin,
        order: ["panel", "logo", "lines"],
        safeMarginPx: marginPx, corner: corner)
}

/// W011 adaptive panel rule: deterministic on the sampled luminance.
public func luminancePanelChoice(_ sampleLuminance: Double) throws
        -> (darkBackground: Bool, panelRGB: (Int, Int, Int),
            textRGB: (Int, Int, Int)) {
    guard sampleLuminance.isFinite,
          sampleLuminance >= 0, sampleLuminance <= 1 else {
        throw WatermarkLayoutError.named(
            "luminance must be a finite number within [0, 1]")
    }
    let darkBg = sampleLuminance > 0.5
    return darkBg
        ? (true, (20, 20, 20), (255, 255, 255))
        : (false, (245, 245, 245), (20, 20, 20))
}
