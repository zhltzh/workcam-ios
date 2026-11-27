// FE-E02: WorkCam Design System tokens (SwiftUI).
// GENERATED from design/tokens/design-tokens.json (w014-rc2) via
// backend/tools/gen_design_system.py - do not hand-edit values; the
// JSON is the single source of truth (WCAG AA verified by
// design/tools/contrast_check.py, 141/141).
// Direction A "Field Pro" is FROZEN as the launch direction; B and C
// remain selectable in the JSON for product review.
// XCODE_PENDING: authored on Windows; Mac compiles.
//
// Platform gate (TM-04 A05): the Windows toolchain has no SwiftUI;
// canImport keeps these tokens Apple-only while the pure-Foundation
// targets build on Windows.

#if canImport(SwiftUI)
import SwiftUI

public enum WCDSpace {
    public static let xs: CGFloat = 4
    public static let sm: CGFloat = 8
    public static let md: CGFloat = 16
    public static let lg: CGFloat = 24
    public static let xl: CGFloat = 32
}

public enum WCDRadius {
    public static let sm: CGFloat = 6
    public static let md: CGFloat = 10
    public static let lg: CGFloat = 16
}

/// Brand layer - Direction A (Field Pro), light theme.
public enum WCDBrandLight {
    public static let primary = Color(red: 0.102, green: 0.208, blue: 0.369)
    public static let primary_hover = Color(red: 0.141, green: 0.290, blue: 0.486)
    public static let primary_subtle = Color(red: 0.910, green: 0.933, blue: 0.969)
    public static let primary_ink = Color(red: 0.102, green: 0.208, blue: 0.369)
    public static let accent = Color(red: 0.910, green: 0.349, blue: 0.047)
    public static let accent_strong = Color(red: 0.761, green: 0.255, blue: 0.047)
    public static let accent_strong_hover = Color(red: 0.678, green: 0.231, blue: 0.035)
    public static let on_accent = Color(red: 1.000, green: 1.000, blue: 1.000)
    public static let bg = Color(red: 0.973, green: 0.976, blue: 0.980)
    public static let surface = Color(red: 1.000, green: 1.000, blue: 1.000)
    public static let border = Color(red: 0.871, green: 0.886, blue: 0.902)
    public static let text_1 = Color(red: 0.106, green: 0.118, blue: 0.137)
    public static let text_2 = Color(red: 0.353, green: 0.392, blue: 0.447)
    public static let text_3 = Color(red: 0.400, green: 0.439, blue: 0.490)
}

/// Brand layer - Direction A (Field Pro), dark theme.
public enum WCDBrandDark {
    public static let primary = Color(red: 0.361, green: 0.616, blue: 1.000)
    public static let primary_hover = Color(red: 0.478, green: 0.710, blue: 1.000)
    public static let primary_subtle = Color(red: 0.102, green: 0.165, blue: 0.227)
    public static let primary_ink = Color(red: 0.361, green: 0.616, blue: 1.000)
    public static let accent = Color(red: 1.000, green: 0.549, blue: 0.259)
    public static let accent_strong = Color(red: 1.000, green: 0.549, blue: 0.259)
    public static let accent_strong_hover = Color(red: 1.000, green: 0.627, blue: 0.361)
    public static let on_accent = Color(red: 0.055, green: 0.067, blue: 0.075)
    public static let bg = Color(red: 0.055, green: 0.067, blue: 0.075)
    public static let surface = Color(red: 0.102, green: 0.118, blue: 0.137)
    public static let border = Color(red: 0.165, green: 0.188, blue: 0.220)
    public static let text_1 = Color(red: 0.914, green: 0.925, blue: 0.937)
    public static let text_2 = Color(red: 0.565, green: 0.604, blue: 0.639)
    public static let text_3 = Color(red: 0.545, green: 0.584, blue: 0.631)
}

/// Evidence status colors - SEMANTIC, SHARED across all directions.
/// Light variants for light surfaces; dark variants for dark surfaces.
public enum WCDEvidence {
    public enum Light {
        public static let none = Color(red: 0.357, green: 0.392, blue: 0.439)
        public static let unverified = Color(red: 0.541, green: 0.373, blue: 0.000)
        public static let signature_valid = Color(red: 0.231, green: 0.357, blue: 0.859)
        public static let verified = Color(red: 0.118, green: 0.435, blue: 0.220)
        public static let failed = Color(red: 0.788, green: 0.165, blue: 0.165)
        public static let anchored = Color(red: 0.373, green: 0.239, blue: 0.769)
    }
    public enum Dark {
        public static let none = Color(red: 0.639, green: 0.671, blue: 0.710)
        public static let unverified = Color(red: 1.000, green: 0.788, blue: 0.302)
        public static let signature_valid = Color(red: 0.478, green: 0.584, blue: 1.000)
        public static let verified = Color(red: 0.318, green: 0.812, blue: 0.400)
        public static let failed = Color(red: 1.000, green: 0.420, blue: 0.420)
        public static let anchored = Color(red: 0.647, green: 0.541, blue: 0.976)
    }
    /// Badge fill alpha per the token spec (mixed over surface).
    public static let badgeAlpha: Double = 0.15
    public static let badgeAlphaFailed: Double = 0.12
    /// The trust ORDER for display sorting (none -> failed states
    /// are independent claims; this is presentation order only).
    /// json.dumps => Swift-legal double-quoted literals (TM-04 fix:
    /// the Python list repr leaked single quotes and never compiled).
    public static let order = ["none", "unverified", "signature_valid", "verified", "failed", "anchored"]
}

/// Annotation color palette (6 colors, shared).
public enum WCDAnnotationPalette {
    public static let c0 = Color(red: 1.000, green: 0.231, blue: 0.188)
    public static let c1 = Color(red: 1.000, green: 0.584, blue: 0.000)
    public static let c2 = Color(red: 1.000, green: 0.800, blue: 0.000)
    public static let c3 = Color(red: 0.204, green: 0.780, blue: 0.349)
    public static let c4 = Color(red: 0.000, green: 0.478, blue: 1.000)
    public static let c5 = Color(red: 0.686, green: 0.322, blue: 0.871)
}

/// Fixed surfaces - do NOT invert in dark mode.
public enum WCDFixedSurfaces {
    public static let report_preview_paper = Color(red: 1.000, green: 1.000, blue: 1.000)
    public static let report_preview_text = Color(red: 0.106, green: 0.118, blue: 0.137)
    public static let camera_stage = Color(red: 0.102, green: 0.102, blue: 0.102)
    public static let shutter_button = Color(red: 1.000, green: 1.000, blue: 1.000)
}
#endif
