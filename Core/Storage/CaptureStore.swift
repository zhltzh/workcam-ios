import Foundation

struct CaptureStore {
    static func save(_ data: Data) throws -> URL {
        try EvidenceStore.capture(data)
    }
}
