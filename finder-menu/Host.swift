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

private func showAlert(_ title: String, _ text: String, style: NSAlert.Style = .warning) {
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = text
    alert.alertStyle = style
    alert.addButton(withTitle: "好")
    NSApp.activate(ignoringOtherApps: true)
    alert.runModal()
}

private struct ProcessingResult: Decodable {
    let title: String
    let message: String
    let failed: [[String]]

    static func error(_ error: Error) -> Self {
        Self(title: "清除失败", message: error.localizedDescription, failed: [[error.localizedDescription]])
    }
}

private final class ProgressStream {
    let counts = Progress(totalUnitCount: -1)
    private(set) var diagnostic = ""

    func read(_ handle: FileHandle) {
        var buffer = Data()
        while true {
            let chunk = handle.availableData
            if chunk.isEmpty { break }
            buffer.append(chunk)
            while let end = buffer.firstIndex(of: 10) {
                receive(String(decoding: buffer[..<end], as: UTF8.self))
                buffer.removeSubrange(...end)
            }
        }
        if !buffer.isEmpty { receive(String(decoding: buffer, as: UTF8.self)) }
    }

    private func receive(_ line: String) {
        let fields = line.split(separator: " ")
        if fields.count == 3, fields[0] == "PROGRESS",
           let done = Int64(fields[1]), let total = Int64(fields[2]), done >= 0, done <= total {
            if counts.totalUnitCount != total { counts.totalUnitCount = total }
            counts.completedUnitCount = done
        } else if diagnostic.count < 1500 {
            diagnostic += String((line + "\n").prefix(1500 - diagnostic.count))
        }
    }
}

private struct ProcessingView: View {
    let counts: Progress

    var body: some View {
        // Sampling keeps a large, fast batch from queuing one UI update per file.
        TimelineView(.periodic(from: .now, by: 0.1)) { _ in
            let total = counts.totalUnitCount
            let done = counts.completedUnitCount
            VStack(alignment: .leading, spacing: 10) {
                if total >= 0 {
                    Text("已处理 \(done) / \(total) 项").monospacedDigit()
                } else {
                    Text("正在统计照片…")
                }
                if total > 0 && (total > 1 || done > 0) {
                    ProgressView(value: Double(done), total: Double(total))
                } else {
                    ProgressView()
                }
            }
            .progressViewStyle(.linear)
            .padding(20)
        }
    }
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
            } catch { showAlert("无法保存设置", error.localizedDescription) }
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
            Section("画质说明") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("清除增益图不会降低 JPG 主图的清晰度或分辨率，但会移除原来的 HDR 高光效果。工具不会重新压缩主图。")
                    Text("实测三份样本，处理前后的主图压缩数据和解码后的每个像素都相同，EXIF 和已有的 ICC 色彩配置也保持一致。")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
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
    private var progressWindow: NSPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do { try prepareRequests() }
        catch {
            showAlert("无法创建菜单请求目录", error.localizedDescription)
            NSApp.terminate(nil)
            return
        }
        guard isWorker else {
            showSettings()
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let results: [ProcessingResult]
            do { results = try self.processRequests() }
            catch { results = [.error(error)] }
            DispatchQueue.main.async {
                for result in results {
                    showAlert(result.title, result.message, style: result.failed.isEmpty ? .informational : .warning)
                }
                if self.settingsWindow == nil { NSApp.terminate(nil) }
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !isWorker { showSettings() }
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !isWorker }

    private func showProgress(_ counts: Progress) {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 340, height: 84),
                            styleMask: [.titled, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.title = "正在清除 JPG HDR 增益图"
        panel.contentView = NSHostingView(rootView: ProcessingView(counts: counts))
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.center()
        panel.orderFrontRegardless()
        progressWindow = panel
    }

    private func showSettings() {
        if settingsWindow == nil {
            do {
                let panel = SettingsPanel(preferences: try Preferences.load())
                let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 510, height: 442),
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
                showAlert("无法读取设置", error.localizedDescription)
                NSApp.terminate(nil)
                return
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    private func processRequests() throws -> [ProcessingResult] {
        let files = FileManager.default
        let lock = open(requests.appendingPathComponent(".worker.lock").path, O_CREAT | O_RDWR, 0o600)
        guard lock >= 0 else { throw failure("无法打开菜单请求目录。") }
        defer { flock(lock, LOCK_UN); close(lock) }
        guard flock(lock, LOCK_EX) == 0 else { throw failure("无法锁定处理队列。") }

        let pending = try files.contentsOfDirectory(at: requests,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        var results: [ProcessingResult] = []
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
                results.append(try process(claimed, backup: Preferences.load().backupEnabled))
            } catch { results.append(.error(error)) }
        }
        return results
    }

    private func process(_ request: URL, backup: Bool) throws -> ProcessingResult {
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
        let input = try FileHandle(forReadingFrom: request)
        defer { try? input.close() }
        process.standardInput = input
        process.arguments = ["-I", "-B", script.path, "--stdin-paths", "--progress"] + (backup ? ["--backup"] : [])
        let output = Pipe()
        let events = Pipe()
        process.standardOutput = output
        process.standardError = events
        let progress = ProgressStream()
        DispatchQueue.main.async { self.showProgress(progress.counts) }
        defer {
            DispatchQueue.main.async {
                self.progressWindow?.close()
                self.progressWindow = nil
            }
        }
        try process.run()
        let reader = DispatchGroup()
        DispatchQueue.global(qos: .userInitiated).async(group: reader) {
            progress.read(events.fileHandleForReading)
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        reader.wait()
        if process.terminationReason == .exit,
           let result = try? JSONDecoder().decode(ProcessingResult.self, from: data),
           process.terminationStatus == (result.failed.isEmpty ? 0 : 1) {
            return result
        }
        let detail = (progress.diagnostic.isEmpty ? String(decoding: data.prefix(1500), as: UTF8.self)
                      : progress.diagnostic).trimmingCharacters(in: .whitespacesAndNewlines)
        throw failure(detail.isEmpty ? "清除程序未返回结果（退出码 \(process.terminationStatus)）。" : detail)
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
