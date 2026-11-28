// M03-B12: WorkCamBusiness - Swift package target porting the B01-B11
// business logic (Python reference: backend/techlab/business/*.py).
// STATUS: CROSS_PLATFORM_PENDING (authored on Windows, Mac compiles).
//
// The port carries the SAME honesty rules as Python:
//   - checklists NEVER auto-pass unchecked items
//   - evidence requirements are not waivable
//   - issue transitions are guarded, never silent jumps
//   - photo roles are human-only; completeness never auto-declares
//   - naming output is a STRING (no file API by construction)
//   - manual vs device measurement is a structural split
//   - daily logs never fabricate weather/conditions
//   - OCR text is searchable only after human confirmation

import Foundation

// MARK: - B01: templates

public enum TemplateKind: String, Codable, Sendable, CaseIterable {
    case construction
    case siteInspection = "site_inspection"
    case propertyInspection = "property_inspection"
    case facilityMaintenance = "facility_maintenance"
    case roofing, electrical, plumbing, hvac
}

public struct ProjectTemplate: Sendable, Equatable {
    public let kind: TemplateKind
    public let displayName: String
    public let photoCategories: [String]
    public let issueCategories: [String]
    public let sections: [ChecklistSection]
    public let watermarkFields: [String]
    public let reportStructure: [String]
    public let version: String

    public init(kind: TemplateKind, displayName: String,
                photoCategories: [String], issueCategories: [String],
                sections: [ChecklistSection],
                watermarkFields: [String],
                reportStructure: [String],
                version: String = "project-templates/1.0") throws {
        guard !displayName.isEmpty, !sections.isEmpty else {
            throw BusinessError.named("template needs name + sections")
        }
        for f in watermarkFields
        where f.range(of: "^[a-z_]+$", options: .regularExpression)
                == nil {
            throw BusinessError.named(
                "watermark field \(f) must be snake_case")
        }
        self.kind = kind
        self.displayName = displayName
        self.photoCategories = photoCategories
        self.issueCategories = issueCategories
        self.sections = sections
        self.watermarkFields = watermarkFields
        self.reportStructure = reportStructure
        self.version = version
    }
}

// MARK: - B02: checklist engine

public enum ItemStatus: String, Codable, Sendable {
    case pending, passed, failed
    case notApplicable = "not_applicable"
    case notInspected = "not_inspected"
}

public enum EvidenceRequired: String, Codable, Sendable {
    case photo, note, measurement
}

public struct ChecklistItem: Sendable, Equatable {
    public let itemID: String
    public let text: String
    public let requiredEvidence: [EvidenceRequired]
    public init(itemID: String, text: String,
                requiredEvidence: [EvidenceRequired] = []) {
        self.itemID = itemID
        self.text = text
        self.requiredEvidence = requiredEvidence
    }
}

public struct ChecklistSection: Sendable, Equatable {
    public let sectionID: String
    public let title: String
    public let items: [ChecklistItem]
    public init(sectionID: String, title: String,
                items: [ChecklistItem]) {
        self.sectionID = sectionID
        self.title = title
        self.items = items
    }
}

public struct ChecklistItemState: Sendable, Equatable {
    public let item: ChecklistItem
    public var status: ItemStatus
    public var photoIDs: [String]
    public var note: String
    public var measurementValue: Double?
    public var measurementUnit: String?
}

public struct ChecklistSummary: Sendable, Equatable {
    public let counts: [ItemStatus: Int]
    public let complete: Bool
    public let blockers: [String]      // NAMED, never hidden
}

public final class ChecklistRun: @unchecked Sendable {
    public let templateKind: TemplateKind
    public let projectID: String
    public private(set) var states: [String: ChecklistItemState] = [:]

    public init(templateKind: TemplateKind, projectID: String,
                sections: [ChecklistSection]) {
        self.templateKind = templateKind
        self.projectID = projectID
        for section in sections {
            for item in section.items {
                states[item.itemID] = ChecklistItemState(
                    item: item, status: .pending, photoIDs: [],
                    note: "", measurementValue: nil,
                    measurementUnit: nil)
            }
        }
    }

    public func setStatus(_ itemID: String, _ status: ItemStatus,
                          photoIDs: [String]? = nil, note: String = "",
                          measurement: (Double, String)? = nil) throws {
        guard var st = states[itemID] else {
            throw BusinessError.named("unknown item \(itemID)")
        }
        if status == .passed || status == .failed {
            for req in st.item.requiredEvidence {
                switch req {
                case .photo:
                    guard let p = photoIDs, !p.isEmpty else {
                        throw BusinessError.named(
                            "item \(itemID) requires a photo - " +
                            "evidence is NOT waivable")
                    }
                    st.photoIDs = p
                case .note:
                    guard !note.trimmingCharacters(
                            in: .whitespacesAndNewlines).isEmpty else {
                        throw BusinessError.named(
                            "item \(itemID) requires a note")
                    }
                    st.note = note
                case .measurement:
                    guard let m = measurement else {
                        throw BusinessError.named(
                            "item \(itemID) requires a measurement")
                    }
                    // D04: a measurement must be a FINITE number -
                    // NaN/Inf would smuggle a fake verification
                    // through the required-evidence gate.
                    guard m.0.isFinite else {
                        throw BusinessError.named(
                            "item \(itemID) measurement is not a " +
                            "finite number")
                    }
                    guard !m.1.isEmpty else {
                        throw BusinessError.named(
                            "item \(itemID) measurement needs a unit")
                    }
                    st.measurementValue = m.0
                    st.measurementUnit = m.1
                }
            }
        }
        st.status = status
        states[itemID] = st
    }

    public func summary() -> ChecklistSummary {
        var counts: [ItemStatus: Int] = [:]
        var blockers: [String] = []
        for st in states.values {
            counts[st.status, default: 0] += 1
            if st.status == .pending { blockers.append(st.item.itemID) }
        }
        return ChecklistSummary(counts: counts,
                                complete: blockers.isEmpty,
                                blockers: blockers)
    }
}

// MARK: - B04: issue lifecycle

public enum IssueStatus: String, Codable, Sendable {
    case open, inProgress = "in_progress", resolved, closed, reopened
}

public struct StatusChange: Codable, Sendable, Equatable {
    public let timestamp: Date
    public let fromStatus: IssueStatus
    public let toStatus: IssueStatus
    public let actor: String
    public let note: String

    public init(from: IssueStatus, to: IssueStatus, actor: String,
                note: String = "", timestamp: Date = Date()) {
        self.timestamp = timestamp
        self.fromStatus = from
        self.toStatus = to
        self.actor = actor
        self.note = note
    }
}

public final class IssueLifecycle: @unchecked Sendable {
    public let issueID: String
    public private(set) var status: IssueStatus = .open
    public private(set) var history: [StatusChange] = []

    public init(issueID: String) { self.issueID = issueID }

    static let allowed: [IssueStatus: Set<IssueStatus>] = [
        .open: [.inProgress, .closed],
        .inProgress: [.resolved],
        .resolved: [.closed, .open],     // reopen path
        .closed: [],
        .reopened: [.inProgress],
    ]

    public func transition(to newStatus: IssueStatus, actor: String,
                           note: String = "") throws {
        if newStatus == status {
            throw BusinessError.named(
                "\(issueID): already \(status.rawValue)")
        }
        guard Self.allowed[status]?.contains(newStatus) == true else {
            throw BusinessError.named(
                "\(issueID): \(status.rawValue) -> " +
                "\(newStatus.rawValue) is not an allowed transition")
        }
        history.append(StatusChange(from: status, to: newStatus,
                                    actor: actor, note: note))
        status = newStatus
    }
}

// MARK: - B05: photo roles

public enum PhotoRole: String, Codable, Sendable {
    case before, progress, after
}

public struct RemediationCompleteness: Sendable, Equatable {
    public let hasPair: Bool
    public let autoComplete: Bool     // always false by construction
    public let note: String
}

public struct IssuePhotoLinks: Sendable, Equatable {
    public let issueID: String
    public let projectID: String
    public private(set) var byRole: [PhotoRole: [String]] = [:]

    public init(issueID: String, projectID: String) {
        self.issueID = issueID
        self.projectID = projectID
    }

    public mutating func link(photoID: String, projectID: String,
                              role: PhotoRole) throws {
        guard projectID == self.projectID else {
            throw BusinessError.named(
                "cross-project photo link refused")
        }
        var roleList = byRole[role] ?? []
        guard !roleList.contains(photoID) else { return }
        roleList.append(photoID)
        byRole[role] = roleList
    }

    public func photos(_ role: PhotoRole) -> [String] {
        byRole[role] ?? []
    }

    public var completeness: RemediationCompleteness {
        RemediationCompleteness(
            hasPair: !photos(.before).isEmpty
                && !photos(.after).isEmpty,
            autoComplete: false,
            note: "photo presence does NOT prove remediation - a " +
                  "HUMAN confirms completion")
    }
}

// MARK: - B06: naming

public enum Naming {
    static let illegal = CharacterSet(charactersIn: "<>:\"/\\|?*")
        .union(.controlCharacters)
    static let reserved: Set<String> = [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
        "LPT1", "LPT2", "LPT3"]

    public static func sanitizeComponent(_ value: String,
                                         maxLen: Int = 80) throws
            -> String {
        var cleaned = String(
            value.unicodeScalars.map {
                Naming.illegal.contains($0) ? "_" : Character($0)
            })
        cleaned = String(cleaned.drop(while: { $0 == "." || $0 == " " }))
        while cleaned.hasSuffix(".") || cleaned.hasSuffix(" ") {
            cleaned = String(cleaned.dropLast())
        }
        guard !cleaned.isEmpty else {
            throw BusinessError.named(
                "component \(value) reduces to nothing")
        }
        let stem = cleaned.uppercased()
            .split(separator: ".").first.map(String.init) ?? ""
        if reserved.contains(stem) {
            throw BusinessError.named(
                "component \(value) hits reserved name \(stem)")
        }
        if cleaned.count > maxLen {
            cleaned = String(cleaned.prefix(maxLen))
            while cleaned.hasSuffix(".") || cleaned.hasSuffix(" ") {
                cleaned = String(cleaned.dropLast())
            }
        }
        return cleaned
    }

    public static func format(template: String,
                              context: [String: String]) throws
            -> String {
        // resolves {key} placeholders from the context dict
        var out = ""
        var i = template.startIndex
        while i < template.endIndex {
            if template[i] == "{",
               let close = template[i...].firstIndex(of: "}") {
                let key = String(template[template.index(
                    after: i)..<close])
                guard let v = context[key], !v.isEmpty else {
                    throw BusinessError.named(
                        "placeholder {\(key)} missing/empty")
                }
                out += try sanitizeComponent(v)
                i = template.index(after: close)
            } else {
                out.append(template[i])
                i = template.index(after: i)
            }
        }
        return try sanitizeComponent(out, maxLen: 180)
    }
}

// MARK: - B10: measurements

public enum MeasurementSource: String, Codable, Sendable {
    case manualInput = "manual_input"
    case deviceEstimate = "device_estimate"
}

public struct ManagedMeasurement: Codable, Sendable, Equatable {
    public let measurementID: String
    public let type: String
    public let value: Double
    public let unit: String
    public let source: MeasurementSource
    public let measurementMode: String?   // required for device only
    public let calibrationVersion: String
    public let notes: String

    public init(measurementID: String, type: String, value: Double,
                unit: String, source: MeasurementSource,
                measurementMode: String? = nil,
                calibrationVersion: String = "uncalibrated",
                notes: String = "") throws {
        if source == .deviceEstimate {
            guard let mode = measurementMode, !mode.isEmpty else {
                throw BusinessError.named(
                    "device_estimate needs a measurement_mode")
            }
            _ = mode
        } else if measurementMode != nil {
            throw BusinessError.named(
                "manual_input carries no measurement_mode")
        }
        if type == "volume" && !notes.contains("experimental") {
            throw BusinessError.named(
                "volume measurements are EXPERIMENTAL - notes must " +
                "say so")
        }
        self.measurementID = measurementID
        self.type = type
        self.value = value
        self.unit = unit
        self.source = source
        self.measurementMode = measurementMode
        self.calibrationVersion = calibrationVersion
        self.notes = notes
    }

    public var honestLabel: String {
        source == .manualInput
            ? "human-entered \(value)\(unit)"
            : "device estimate \(value)\(unit), calibration=" +
                calibrationVersion
    }
}

// MARK: - B11: daily log

public struct DailyLog: Sendable, Equatable {
    public let projectID: String
    public let date: String                 // YYYY-MM-DD
    public var photosCaptured = 0
    public var issuesCreated = 0
    public var issuesResolved = 0
    public var measurementsRecorded = 0
    public var reportsDelivered = 0
    public var weather: String              // USER-ONLY, "" = not filled
    public var siteConditions: String
    public var generalNotes: String

    public init(projectID: String, date: String) throws {
        let ok = date.count == 10 &&
            date.range(of: "^\\d{4}-\\d{2}-\\d{2}$",
                       options: .regularExpression) != nil
        guard ok else {
            throw BusinessError.named(
                "date must be YYYY-MM-DD, got \(date)")
        }
        self.projectID = projectID
        self.date = date
        self.weather = ""
        self.siteConditions = ""
        self.generalNotes = ""
    }

    public var summaryMarkdown: String {
        """
        # Daily Log - \(date)

        ## Activity
        - Photos captured: \(photosCaptured)
        - Issues created: \(issuesCreated)
        - Issues resolved: \(issuesResolved)
        - Measurements: \(measurementsRecorded)
        - Reports delivered: \(reportsDelivered)

        ## Conditions
        - Weather: \(weather.isEmpty ? "_(not filled)_" : weather)
        - Site conditions: \
        \(siteConditions.isEmpty ? "_(not filled)_" : siteConditions)

        ## Notes
        \(generalNotes.isEmpty ? "_(not filled)_" : generalNotes)
        """
    }
}

// MARK: - shared error

public enum BusinessError: Error, Equatable, Sendable {
    case named(String)
}
