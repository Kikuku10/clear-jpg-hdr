import AppKit
import Darwin
import FinderSync

@objc(FinderMenu)
final class FinderMenu: FIFinderSync {
    private var nextTag = 0
    private var pendingSelections: [Int: [URL]] = [:]

    // Sandbox home APIs return the extension container, not the account's home.
    private var requestsDirectory: URL? {
        guard let home = getpwuid(getuid())?.pointee.pw_dir else { return nil }
        return URL(fileURLWithPath: String(cString: home), isDirectory: true)
            .appendingPathComponent("Library/Application Support/ClearJPGHDR/Requests", isDirectory: true)
    }

    private var menuEnabled: Bool {
        guard let preferencesURL = requestsDirectory?.appendingPathComponent("preferences.plist"),
              let data = try? Data(contentsOf: preferencesURL),
              let preferences = try? PropertyListDecoder().decode([String: Bool].self, from: data)
        else { return true }
        return preferences["menuEnabled"] ?? true
    }

    override init() {
        super.init()
        FIFinderSyncController.default().directoryURLs = [URL(fileURLWithPath: "/")]
    }

    override func menu(for menuKind: FIMenuKind) -> NSMenu? {
        guard menuEnabled else { return nil }
        let controller = FIFinderSyncController.default()
        let urls: [URL]
        switch menuKind {
        case .contextualMenuForItems:
            urls = controller.selectedItemURLs() ?? []
        case .contextualMenuForContainer:
            urls = controller.targetedURL().map { [$0] } ?? []
        default:
            return nil
        }
        let candidates = urls.filter { url in
            guard url.isFileURL else { return false }
            return ["jpg", "jpeg"].contains(url.pathExtension.lowercased())
                || url.hasDirectoryPath
                || (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
        }
        guard !candidates.isEmpty else { return nil }

        let item = NSMenuItem(title: "清除 JPG HDR 增益图", action: #selector(clearGainMap(_:)), keyEquivalent: "")
        item.target = self
        // FinderSync serialises tag but does not preserve representedObject.
        nextTag += 1
        pendingSelections = [nextTag: candidates]
        item.tag = nextTag
        item.image = NSImage(systemSymbolName: "sun.max", accessibilityDescription: nil)
        let menu = NSMenu(title: "")
        menu.addItem(item)
        return menu
    }

    @objc private func clearGainMap(_ sender: NSMenuItem) {
        guard menuEnabled else { return }
        guard let urls = pendingSelections.removeValue(forKey: sender.tag), !urls.isEmpty else {
            showFailure("无法读取本次选择，请重新右键选择图片或文件夹。")
            return
        }
        guard let requestsURL = requestsDirectory else {
            showFailure("找不到当前用户的主目录。")
            return
        }
        let requestURL = requestsURL.appendingPathComponent(UUID().uuidString + ".json")
        let pendingURL = requestURL.appendingPathExtension("pending")
        do {
            try FileManager.default.createDirectory(at: requestsURL, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let data = try JSONSerialization.data(withJSONObject: urls.map { $0.path })
            try data.write(to: pendingURL, options: .withoutOverwriting)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: pendingURL.path)
            try FileManager.default.moveItem(at: pendingURL, to: requestURL)
        } catch {
            try? FileManager.default.removeItem(at: pendingURL)
            showFailure("无法写入处理请求。请先打开「清除JPG HDR小工具」，然后重试。\n" + error.localizedDescription)
            return
        }
        let hostURL = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        // NSWorkspace ignores launch arguments from sandboxed callers.
        let workerURL = hostURL.appendingPathComponent("Contents/Helpers/ClearJPGHDRWorker.app", isDirectory: true)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: workerURL, configuration: configuration) { _, error in
            if let error = error {
                try? FileManager.default.removeItem(at: requestURL)
                self.showFailure("无法启动照片处理程序。\n" + error.localizedDescription)
            }
        }
    }

    private func showFailure(_ message: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "清除 JPG HDR 增益图失败"
            alert.informativeText = message
            alert.addButton(withTitle: "好")
            alert.window.level = .modalPanel
            alert.window.orderFrontRegardless()
            alert.runModal()
        }
    }
}
