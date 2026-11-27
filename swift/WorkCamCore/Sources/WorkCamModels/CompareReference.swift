// TM-04 E08 (was TM-03 C07): Before/After compare core logic +
// SwiftUI reference. The ENGINE part is pure Swift (testable on
// Windows); the VIEW part sits behind the SwiftUI platform gate.
//
// RED LINES (selection review + task sheet):
//   - auto-registration (E07) output is ONLY ever a PROPOSAL; it
//     becomes active alignment when the user accepts or adjusts it.
//     Nothing auto-applies.
//   - alignment is DISPLAY-SPACE ONLY: it produces offsets for
//     rendering, never derivative files, never originals changes.
//   - rejecting the proposal leaves the pair unaligned - no
//     fallback "helpful" nudge.

import Foundation

// MARK: - compare modes

public enum CompareMode: String, Codable, Sendable, CaseIterable {
    case sideBySide = "side_by_side"
    case slider
    case opacity
    case manualAlign = "manual_align"
}

// MARK: - alignment session (pure logic, UI-free)

/// The lifecycle of ONE before/after alignment attempt.
public struct AlignmentSession: Sendable, Equatable {
    public enum Stage: Equatable, Sendable {
        case unproposed
        /// Auto-registration proposed (dx, dy); confidence carries
        /// the E07 gate values so the UI can show them honestly.
        case proposed(dx: Double, dy: Double,
                      sharpness: Double, ncc: Double)
        case accepted(dx: Double, dy: Double)
        case adjusted(dx: Double, dy: Double)
        case rejected
    }

    public var stage: Stage
    public let mode: CompareMode

    public init(mode: CompareMode = .sideBySide) {
        self.stage = .unproposed
        self.mode = mode
    }

    private init(mode: CompareMode, stage: Stage) {
        self.mode = mode
        self.stage = stage
    }

    /// Record an auto-registration proposal (E07 result). This is
    /// the ONLY entry point for automatic estimates, and it only
    /// ever sets the `proposed` stage.
    public func receiveProposal(dx: Double, dy: Double,
                                sharpness: Double, ncc: Double)
            -> AlignmentSession {
        AlignmentSession(
            mode: mode,
            stage: .proposed(dx: dx, dy: dy,
                             sharpness: sharpness, ncc: ncc))
    }

    /// User accepted the proposal verbatim.
    public func acceptProposal() -> AlignmentSession {
        guard case .proposed(let dx, let dy, _, _) = stage else {
            return self
        }
        return AlignmentSession(mode: mode,
                                stage: .accepted(dx: dx, dy: dy))
    }

    /// User dragged from the proposal by (deltaX, deltaY) -
    /// typically a few pixels of fine correction.
    public func adjustProposal(deltaX: Double, deltaY: Double)
            -> AlignmentSession {
        guard case .proposed(let dx, let dy, _, _) = stage else {
            return self
        }
        return AlignmentSession(
            mode: mode,
            stage: .adjusted(dx: dx + deltaX, dy: dy + deltaY))
    }

    /// User rejected the proposal: back to unaligned, permanently
    /// for this session (no re-proposal loop).
    public func rejectProposal() -> AlignmentSession {
        AlignmentSession(mode: mode, stage: .rejected)
    }

    /// Manual alignment WITHOUT any proposal: the user aligned the
    /// pair by hand from scratch.
    public func setManualAlignment(dx: Double, dy: Double)
            -> AlignmentSession {
        AlignmentSession(mode: mode, stage: .adjusted(dx: dx, dy: dy))
    }

    /// The display-space offset currently in effect (nil while
    /// merely proposed or rejected).
    public var activeOffset: (dx: Double, dy: Double)? {
        switch stage {
        case .accepted(let dx, let dy),
             .adjusted(let dx, let dy):
            return (dx, dy)
        default:
            return nil
        }
    }

    /// Whether the E07 proposal is pending a user decision.
    public var hasPendingProposal: Bool {
        if case .proposed = stage { return true }
        return false
    }
}

// MARK: - SwiftUI reference (Apple-only; Windows builds skip)

#if canImport(SwiftUI)
import SwiftUI

/// Minimal reference: mode switcher + slider/opacity bindings +
/// manual-align drag. Mac already renders photos; this pins the
/// STATE CONTRACT the production view must honor.
public struct CompareView: View {
    public let beforeImageName: String
    public let afterImageName: String
    @State public var session = AlignmentSession()
    @State public var sliderValue: Double = 0.5
    @State public var opacityValue: Double = 0.5

    public init(beforeImageName: String, afterImageName: String) {
        self.beforeImageName = beforeImageName
        self.afterImageName = afterImageName
    }

    public var body: some View {
        VStack(spacing: 12) {
            Picker("Compare Mode", selection: .constant(
                session.mode)) {
                ForEach(CompareMode.allCases, id: \.rawValue) { m in
                    Text(m.rawValue).tag(m)
                }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("compareModePicker")

            ZStack {
                Image(beforeImageName).resizable().scaledToFit()
                Image(afterImageName).resizable().scaledToFit()
                    .opacity(opacityValue)
                    .accessibilityIdentifier("compareAfterLayer")
            }
            .frame(maxWidth: .infinity)

            if session.hasPendingProposal {
                HStack {
                    Text("Auto alignment proposed. " +
                         "Your photos were not modified.")
                        .font(.footnote)
                    Button("Accept") {
                        session = session.acceptProposal()
                    }
                    .accessibilityIdentifier("compareAcceptProposal")
                    Button("Reject") {
                        session = session.rejectProposal()
                    }
                    .accessibilityIdentifier("compareRejectProposal")
                }
            }
        }
        .padding()
    }
}
#endif
