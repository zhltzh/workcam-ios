// swift-tools-version:5.9
// WorkCamCore - WorkCam universal Swift components (TM56).
//
// STATUS: CROSS_PLATFORM_PENDING - authored on Windows (no Swift
// toolchain available there); Mac compiles and runs the test suite.
// Windows certifies the PYTHON reference implementation and the
// shared golden vectors (test-vectors/geometry/), never Swift results.
//
// Platform policy: Foundation only. No UIKit/SwiftUI/ARKit/CoreLocation
// imports in ANY target of this package - Apple-specific layers are
// injected via protocols at the app level.

import PackageDescription

let package = Package(
    name: "WorkCamCore",
    products: [
        .library(name: "WorkCamGeometry", targets: ["WorkCamGeometry"]),
        .library(name: "WorkCamModels", targets: ["WorkCamModels"]),
        .library(name: "WorkCamEvidence", targets: ["WorkCamEvidence"]),
        .library(name: "WorkCamTime", targets: ["WorkCamTime"]),
        .library(name: "WorkCamLocation", targets: ["WorkCamLocation"]),
        .library(name: "WorkCamSync", targets: ["WorkCamSync"]),
    ],
    targets: [
        .target(name: "WorkCamGeometry"),
        .target(name: "WorkCamModels"),
        .target(name: "WorkCamEvidence", dependencies: []),
        .target(name: "WorkCamTime", dependencies: []),
        .target(name: "WorkCamLocation", dependencies: ["WorkCamGeometry"]),
        .target(name: "WorkCamSync", dependencies: ["WorkCamModels"]),
        .testTarget(
            name: "WorkCamCoreTests",
            dependencies: [
                "WorkCamGeometry", "WorkCamModels", "WorkCamEvidence",
                "WorkCamTime", "WorkCamLocation", "WorkCamSync",
            ],
            resources: [
                // copied verbatim from
                // test-vectors/geometry/geometry-golden-vectors.json
                // (version geometry-vectors/1.0) - keep in sync via
                // the generator script when vectors change.
                .copy("Resources/geometry-golden-vectors.json"),
            ]),
    ]
)
