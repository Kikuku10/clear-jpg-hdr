import AppKit
import Darwin
import FinderSync
import Foundation
import SwiftUI

private let appName = "清除JPG HDR小工具"
private let isWorker = Bundle.main.bundleIdentifier == "local.mimi.ClearJPGHDR.Worker"
private let requests = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/ClearJPGHDR/Requests", isDirectory: true)

private func prepareRequests() throws {
    try FileManager.default.createDirectory(at: requests, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
}

private struct Preferences: Codable {
    var menuEnabled = true
    var backupEnabled = false

    private static let url = requests.appendingPathComponent("preferences.plist")

    static func load() throws -> Self {
        do { return try PropertyListDecoder().decode(Self.self, from: Data(contentsOf: url)) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {
            return Self()
        }
    }

    func save() throws {
        try PropertyListEncoder().encode(self).write(to: Self.url, options: .atomic)
    }
}

private func showError(_ title: String, _ text: String) {
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = text
    alert.alertStyle = .warning
    alert.addButton(withTitle: "好")
    NSApp.activate(ignoringOtherApps: true)
    alert.runModal()
}

private struct SettingsPanel: View {
    @State var preferences: Preferences
    @State private var extensionEnabled = FIFinderSyncController.isExtensionEnabled

    private func setting(_ key: WritableKeyPath<Preferences, Bool>) -> Binding<Bool> {
        Binding(get: { preferences[keyPath: key] }, set: { value in
            var updated = preferences
            updated[keyPath: key] = value
            do {
                try updated.save()
                preferences = updated
            } catch { showError("无法保存设置", error.localizedDescription) }
        })
    }

    var body: some View {
        Form {
            Section("Finder 右键菜单") {
                Toggle("显示「清除 JPG HDR 增益图」", isOn: setting(\.menuEnabled))
                    .toggleStyle(.switch)
                    .accessibilityIdentifier("menuEnabled")
                LabeledContent("Finder 扩展") {
                    Text(extensionEnabled ? "已启用" : "未启用")
                        .foregroundStyle(.secondary)
                    Button("管理扩展…") { FIFinderSyncController.showExtensionManagementInterface() }
                }
            }
            Section {
                Toggle("保留原图备份", isOn: setting(\.backupEnabled))
                    .toggleStyle(.switch)
                    .accessibilityIdentifier("backupEnabled")
            } header: {
                Text("照片处理")
            } footer: {
                if preferences.backupEnabled {
                    Text("例如，照片.jpg 的原图保存在同一文件夹的 照片.jpg_original。已有同名备份时会停止处理这张照片。")
                }
            }
        }
        .formStyle(.grouped)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            extensionEnabled = FIFinderSyncController.isExtensionEnabled
        }
    }
}

final class HostDelegate: NSObject, NSApplicationDelegate {
    private var settingsWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do { try prepareRequests() }
        catch {
            showError("无法创建菜单请求目录", error.localizedDescription)
            NSApp.terminate(nil)
            return
        }
        guard isWorker else {
            showSettings()
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var errors: [String] = []
            do { errors = try self.processRequests() }
            catch { errors = [error.localizedDescription] }
            DispatchQueue.main.async {
                if !errors.isEmpty {
                    showError("清除 JPG HDR 增益图失败", errors.prefix(5).joined(separator: "\n"))
                }
                if self.settingsWindow == nil { NSApp.terminate(nil) }
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !isWorker { showSettings() }
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func showSettings() {
        if settingsWindow == nil {
            do {
                let panel = SettingsPanel(preferences: try Preferences.load())
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 510, height: 300),
                                      styleMask: [.titled, .closable, .miniaturizable],
                                      backing: .buffered, defer: false)
                window.title = appName
                window.contentView = NSHostingView(rootView: panel)
                window.isReleasedWhenClosed = false
                window.center()
                settingsWindow = window

                NSApp.setActivationPolicy(.regular)
                let menu = NSMenu()
                let appItem = NSMenuItem()
                menu.addItem(appItem)
                let appMenu = NSMenu()
                appMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
                appMenu.addItem(withTitle: "退出" + appName, action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
                appItem.submenu = appMenu
                NSApp.mainMenu = menu
            } catch {
                showError("无法读取设置", error.localizedDescription)
                NSApp.terminate(nil)
                return
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    private func processRequests() throws -> [String] {
        let files = FileManager.default
        let lock = open(requests.appendingPathComponent(".worker.lock").path, O_CREAT | O_RDWR, 0o600)
        guard lock >= 0 else { throw failure("无法打开菜单请求目录。") }
        defer { flock(lock, LOCK_UN); close(lock) }
        guard flock(lock, LOCK_EX) == 0 else { throw failure("无法锁定处理队列。") }

        let pending = try files.contentsOfDirectory(at: requests,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        var errors: [String] = []
        for request in pending {
            do {
                let attributes = try request.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
                guard attributes.isRegularFile == true, attributes.isSymbolicLink != true else { continue }
                let claimed = request.appendingPathExtension("processing")
                try files.moveItem(at: request, to: claimed)
                // These are our own one-use request records, never the selected photographs.
                defer { try? files.removeItem(at: claimed) }
                let data = try Data(contentsOf: claimed)
                guard let paths = try JSONSerialization.jsonObject(with: data) as? [String],
                      !paths.isEmpty, paths.allSatisfy({ $0.hasPrefix("/") && !$0.contains("\0") }) else {
                    throw failure("菜单请求格式无效。")
                }
                try process(paths, backup: Preferences.load().backupEnabled)
            } catch { errors.append(error.localizedDescription) }
        }
        return errors
    }

    private func process(_ paths: [String], backup: Bool) throws {
        let hostURL = Bundle.main.bundleURL.deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        guard let script = Bundle(url: hostURL)?.url(forResource: "clear_hdr", withExtension: "py") else {
            throw failure("找不到清除程序。")
        }
        let process = Process()
        let python = hostURL.appendingPathComponent("Contents/Helpers/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw failure("App 内的 Python 不完整，请重新安装。")
        }
        process.executableURL = python
        process.arguments = ["-I", "-B", script.path] + (backup ? ["--backup"] : []) + ["--"] + paths
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            throw failure(String(data: data, encoding: .utf8) ?? "清除程序运行失败。")
        }
    }

    private func failure(_ message: String) -> NSError {
        NSError(domain: "ClearJPGHDR", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}

@main
enum ClearHDRApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = HostDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { application.run() }
    }
}
