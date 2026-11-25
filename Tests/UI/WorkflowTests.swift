import XCTest

final class WorkflowTests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    override func tearDown() {
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = "Night 05 workflow final screen"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testProjectAndDeliveryThroughUI() {
        let app = XCUIApplication()
        app.launchArguments = ["--night04-fixture"]
        app.launch()

        let fixture = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "PASS · Synthetic")).firstMatch
        XCTAssertTrue(fixture.waitForExistence(timeout: 40))
        app.buttons["Projects"].tap()
        app.buttons["New Project"].tap()
        let input = app.alerts.textFields.firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("UI Acceptance Site")
        app.alerts.buttons["Create"].tap()
        XCTAssertTrue(app.buttons["UI Acceptance Site"].waitForExistence(timeout: 5))

        app.buttons["UI Acceptance Site"].press(forDuration: 1)
        app.buttons["Archive Project"].tap()
        XCTAssertFalse(app.buttons["UI Acceptance Site"].exists)
        let archiveFilter = app.buttons["projectArchiveFilter"]
        if !archiveFilter.exists { app.swipeDown() }
        XCTAssertTrue(archiveFilter.waitForExistence(timeout: 5))
        archiveFilter.tap()
        XCTAssertTrue(app.buttons["UI Acceptance Site"].waitForExistence(timeout: 5))
        archiveFilter.tap()

        app.buttons["Synthetic Test Site"].tap()
        let photo = app.buttons.matching(identifier: "projectPhotoRow").firstMatch
        reveal(photo, in: app)
        photo.tap()
        XCTAssertTrue(app.navigationBars["Project Photo"].waitForExistence(timeout: 5))
        let image = app.descendants(matching: .any)["annotationCanvas"].firstMatch
        reveal(image, in: app)
        image.coordinate(withNormalizedOffset: CGVector(dx: 0.25, dy: 0.3)).tap()
        let save = app.buttons["Save Annotations"]
        reveal(save, in: app)
        save.tap()
        let watermark = app.buttons["Generate Watermark Preview"]
        reveal(watermark, in: app)
        watermark.tap()
        let generated = app.images["Generated watermark preview"]
        reveal(generated, in: app)
        XCTAssertTrue(generated.exists)

        app.navigationBars["Project Photo"].buttons.firstMatch.tap()
        let export = app.buttons["Export PDF"]
        reveal(export, in: app)
        export.tap()
        let preview = app.buttons["Preview PDF"]
        XCTAssertTrue(preview.waitForExistence(timeout: 15))
        reveal(preview, in: app)
        preview.tap()
        let previewClose = app.buttons.matching(NSPredicate(format: "identifier == %@ OR label == %@", "QLOverlayDoneButtonAccessibilityIdentifier", "Done")).firstMatch
        XCTAssertTrue(previewClose.waitForExistence(timeout: 12))
    }

    func testProjectSearch() {
        let app = XCUIApplication()
        app.launchArguments = ["--night04-fixture"]
        app.launch()
        let fixture = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "PASS · Synthetic")).firstMatch
        XCTAssertTrue(fixture.waitForExistence(timeout: 40))
        app.buttons["Projects"].tap()
        let search = app.searchFields.firstMatch
        if !search.isHittable { app.swipeDown() }
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap()
        search.typeText("No Matching Project")
        XCTAssertFalse(app.buttons["Synthetic Test Site"].exists)
        XCTAssertTrue(app.staticTexts["No Projects"].exists)
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<6 where !element.isHittable { app.swipeUp(velocity: .slow) }
        for _ in 0..<6 where !element.isHittable { app.swipeDown(velocity: .slow) }
        XCTAssertTrue(element.isHittable)
    }
}
