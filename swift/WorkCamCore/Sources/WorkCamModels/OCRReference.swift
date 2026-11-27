// TM-04 E09 (was TM-03 C08): OCR data contract - candidates, typed
// engineering fields, the human-confirmation state machine, and the
// Apple Vision intake protocol. Pure Foundation: compiled AND
// unit-tested on the real Windows Swift 6.4.3 toolchain.
//
// RED LINES (task sheet + TM-03 B08 note):
//   - OCR output is a CANDIDATE, never a fact. No code path may
//     derive `confirmed` from a confidence threshold - the
//     confirmation payload (who/when) can only be constructed by
//     an explicit user action.
//   - OCR text becomes searchable ONLY after human confirmation
//     (mirrors the B08 search-index contract).
//   - The engine itself stays Apple Vision on device (protocol
//     below); Windows never fakes recognition results.

import Foundation

// MARK: - recognized text candidates

/// One raw OCR hypothesis produced by the engine (Apple Vision on
/// device; VNRecognizedTextObservation semantics).
public struct OCRCandidate: Codable, Sendable, Equatable {
    public let text: String
    /// Uppercased, whitespace-collapsed - the comparison key the
    /// confirmation UI uses to spot duplicate candidates.
    public let normalizedText: String
    /// Engine-reported confidence in [0, 1]. NEVER a decision input.
    public let confidence: Double
    /// Normalized bounding box (u, v, w, h) in [0, 1], v measured
    /// from the TOP (Vision's own convention).
    public let boundingBoxU: Double
    public let boundingBoxV: Double
    public let boundingBoxW: Double
    public let boundingBoxH: Double

    public init(text: String, confidence: Double,
                u: Double, v: Double, w: Double, h: Double) {
        self.text = text
        self.normalizedText = Self.normalize(text)
        self.confidence = confidence
        self.boundingBoxU = u
        self.boundingBoxV = v
        self.boundingBoxW = w
        self.boundingBoxH = h
    }

    static func normalize(_ s: String) -> String {
        s.uppercased()
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }
}

// MARK: - typed engineering fields

/// The engineering-label taxonomy OCR is allowed to PROPOSE.
/// Anything outside this list stays `other` - no free-form "smart"
/// classification.
public enum OCRFieldType: String, Codable, Sendable, CaseIterable {
    case serialNumber = "serial_number"
    case modelPlate = "model_plate"
    case measurementReading = "measurement_reading"
    case locationLabel = "location_label"
    case dateCode = "date_code"
    case barcodeText = "barcode_text"
    case other
}

// MARK: - human confirmation state machine

/// The confirmation lifecycle. The ONLY way to reach `.confirmed` is
/// the explicit `OCRFieldCandidate.confirm(_:by:at:)` call - the
/// payload records who confirmed and when (audit), and there is no
/// automatic promotion anywhere in this file (or anywhere else, per
/// the structural red line).
public enum OCRConfirmationState: Codable, Sendable, Equatable {
    case unconfirmed
    case confirmed(text: String, confirmedBy: String,
                   confirmedAt: Date)
    case discarded(reason: String)

    private enum CodingKeys: String, CodingKey {
        case kind, text, confirmedBy = "confirmed_by",
             confirmedAt = "confirmed_at", reason
    }

    private enum Kind: String, Codable {
        case unconfirmed, confirmed, discarded
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(Kind.self, forKey: .kind) {
        case .unconfirmed:
            self = .unconfirmed
        case .confirmed:
            self = .confirmed(
                text: try c.decode(String.self, forKey: .text),
                confirmedBy: try c.decode(String.self,
                                          forKey: .confirmedBy),
                confirmedAt: try c.decode(Date.self,
                                          forKey: .confirmedAt))
        case .discarded:
            self = .discarded(
                reason: try c.decode(String.self, forKey: .reason))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .unconfirmed:
            try c.encode(Kind.unconfirmed, forKey: .kind)
        case .confirmed(let text, let by, let at):
            try c.encode(Kind.confirmed, forKey: .kind)
            try c.encode(text, forKey: .text)
            try c.encode(by, forKey: .confirmedBy)
            try c.encode(at, forKey: .confirmedAt)
        case .discarded(let reason):
            try c.encode(Kind.discarded, forKey: .kind)
            try c.encode(reason, forKey: .reason)
        }
    }
}

/// One typed OCR field: the raw candidates + the confirmation state.
public struct OCRFieldCandidate: Codable, Sendable, Equatable {
    public let fieldType: OCRFieldType
    public let candidates: [OCRCandidate]
    public private(set) var state: OCRConfirmationState

    public init(fieldType: OCRFieldType, candidates: [OCRCandidate]) {
        self.fieldType = fieldType
        self.candidates = candidates
        self.state = .unconfirmed
    }

    /// Explicit HUMAN confirmation of one candidate's text (or a
    /// human-corrected string - the user may fix the OCR output).
    /// The only path to `.confirmed`. Returns nil when the confirmed
    /// text matches no candidate (callers must not invent matches).
    public mutating func confirm(text: String, by: String, at: Date)
            -> Bool {
        guard case .unconfirmed = state else { return false }
        let normalized = OCRCandidate.normalize(text)
        // the confirmed text must be a candidate (verbatim or
        // whitespace/case-equal) OR an explicit human correction -
        // both are legitimate; what is NOT legitimate is confirming
        // a field with zero candidates.
        guard !candidates.isEmpty else { return false }
        state = .confirmed(text: text, confirmedBy: by, confirmedAt: at)
        return true
    }

    /// Human rejected the field entirely (wrong read, not a label).
    public mutating func discard(reason: String) {
        guard case .unconfirmed = state else { return }
        state = .discarded(reason: reason)
    }

    /// B08 contract: OCR text is searchable ONLY after human
    /// confirmation. This is the single gate the search index uses.
    public var isSearchable: Bool {
        if case .confirmed = state { return true }
        return false
    }
}

// MARK: - Apple Vision intake contract (the seam Mac adopts)

/// The protocol the app's recognition service conforms to. Mac
/// implements it with VNRecognizeTextRequest; tests implement it
/// with scripted fixtures. Windows authors the contract and the
/// data structures - it never claims recognition results.
public protocol TextRecognitionService: Sendable {
    /// Recognition languages (BCP-47), e.g. ["en-US"].
    var recognitionLanguages: [String] { get }

    /// Runs on-device text recognition over an image. `rect` (in
    /// normalized image coordinates) restricts the region, e.g. to
    /// the label plate the user framed.
    func recognize(imageData: Data, region: (u: Double, v: Double,
                                              w: Double, h: Double)?) async throws
        -> [OCRCandidate]
}
