import AppKit
import Foundation

/// Portskill native shell: Dock icon (regular activation) + menubar status item.
/// Spawns `python3 -m port_registry_app --no-open` and keeps it alive as a child.
/// Built into macos/Portskill.app or standalone PortskillMenu.app.
@main
enum PortskillNativeMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        // Dock-visible unless PORTSKILL_MENU_ONLY=1
        if ProcessInfo.processInfo.environment["PORTSKILL_MENU_ONLY"] == "1" {
            app.setActivationPolicy(.accessory)
        } else {
            app.setActivationPolicy(.regular)
        }
        app.run()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var serverProcess: Process?
    private var serverPipe: Pipe?
    private var stopping = false

    /// Prefer sticky ~/.config/port-registry/listen.json; fall back to historical :8765.
    private var uiURL: URL {
        resolvedListenURL() ?? URL(string: "http://127.0.0.1:8765/")!
    }

    private func resolvedListenURL() -> URL? {
        let path = NSString(string: "~/.config/port-registry/listen.json").expandingTildeInPath
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        if let str = obj["ui_url"] as? String, let u = URL(string: str) {
            return u
        }
        if let port = obj["port"] as? Int {
            return URL(string: "http://127.0.0.1:\(port)/")
        }
        if let port = obj["port"] as? NSNumber {
            return URL(string: "http://127.0.0.1:\(port.intValue)/")
        }
        return nil
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        let external = ProcessInfo.processInfo.environment["PORTSKILL_SERVER_EXTERNAL"] == "1"
        if !external {
            startServerIfNeeded()
        } else {
            NSLog("Portskill: external server mode (LaunchAgent owns python)")
        }
        // Open browser once on interactive double-click (not LaunchAgent)
        if ProcessInfo.processInfo.environment["PORTSKILL_KEEPALIVE"] != "1",
           ProcessInfo.processInfo.environment["PORTSKILL_NO_OPEN"] != "1" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
                guard let self else { return }
                NSWorkspace.shared.open(self.uiURL)
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopping = true
        stopServer()
    }

    private func packageRoot() -> String {
        if let root = ProcessInfo.processInfo.environment["PORTSKILL_PACKAGE_ROOT"], !root.isEmpty {
            return root
        }
        // …/Portskill.app/Contents/MacOS/Portskill
        let exe = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let macos = exe.deletingLastPathComponent() // MacOS
        let contents = macos.deletingLastPathComponent() // Contents
        // Bundled layout: Contents/Resources/python/port_registry_app
        let embedded = contents.appendingPathComponent("Resources/python")
        let embeddedModule = embedded.appendingPathComponent("port_registry_app")
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: embeddedModule.path, isDirectory: &isDir), isDir.boolValue {
            return embedded.path
        }
        // Dev fallback: …/macos/Portskill.app → ../../../../ (repo package root)
        let app = contents.deletingLastPathComponent() // Portskill.app
        let macosDir = app.deletingLastPathComponent() // macos
        return macosDir.deletingLastPathComponent().path
    }

    private func setupMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            if let img = loadMenuImage() {
                img.isTemplate = true
                button.image = img
            } else {
                button.title = "PS"
            }
            button.toolTip = "Portskill"
        }
        let menu = NSMenu()
        let title = NSMenuItem(title: "Portskill", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        menu.addItem(NSMenuItem.separator())
        let open = NSMenuItem(title: "Open UI", action: #selector(openUI), keyEquivalent: "o")
        open.target = self
        menu.addItem(open)
        let check = NSMenuItem(title: "Check server…", action: #selector(checkServer), keyEquivalent: "")
        check.target = self
        menu.addItem(check)
        menu.addItem(NSMenuItem.separator())
        let quit = NSMenuItem(title: "Quit Portskill", action: #selector(quitAll), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        statusItem = item
    }

    private func loadMenuImage() -> NSImage? {
        let root = packageRoot()
        let candidates = [
            URL(fileURLWithPath: root).appendingPathComponent("macos/PortskillMenu/Resources/MenuIcon.png"),
            URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
                .deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent("Resources/MenuIcon.png"),
        ]
        for url in candidates {
            if let img = NSImage(contentsOf: url) { return img }
        }
        return nil
    }

    private func resolvePython() -> String? {
        let candidates = ["python3", "/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"]
        for c in candidates {
            if c.hasPrefix("/") {
                if FileManager.default.isExecutableFile(atPath: c) { return c }
            } else if let found = which(c) {
                return found
            }
        }
        return nil
    }

    private func which(_ name: String) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        p.arguments = [name]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        do {
            try p.run()
            p.waitUntilExit()
        } catch { return nil }
        guard p.terminationStatus == 0 else { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        let s = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (s?.isEmpty == false) ? s : nil
    }

    private func serverAlreadyListening() -> Bool {
        var req = URLRequest(url: uiURL, timeoutInterval: 0.4)
        req.httpMethod = "GET"
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let http = resp as? HTTPURLResponse { ok = (200..<500).contains(http.statusCode) }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 0.5)
        return ok
    }

    private func startServerIfNeeded() {
        if serverAlreadyListening() {
            NSLog("Portskill: server already listening at %@", self.uiURL.absoluteString)
            return
        }
        guard let py = resolvePython() else {
            let alert = NSAlert()
            alert.messageText = "Portskill"
            alert.informativeText = "python3 not found on PATH."
            alert.runModal()
            return
        }
        let root = packageRoot()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: py)
        proc.arguments = ["-m", "port_registry_app", "--no-open"]
        var env = ProcessInfo.processInfo.environment
        let existing = env["PYTHONPATH"] ?? ""
        env["PYTHONPATH"] = existing.isEmpty ? root : "\(root):\(existing)"
        env["PORTSKILL_PACKAGE_ROOT"] = root
        proc.environment = env
        proc.currentDirectoryURL = URL(fileURLWithPath: root)
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        serverPipe = pipe
        proc.terminationHandler = { [weak self] p in
            NSLog("Portskill: server exited status=%d", p.terminationStatus)
            guard let self, !self.stopping else { return }
            // Brief delay then restart (LaunchAgent also KeepAlive's this app)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                if !self.stopping { self.startServerIfNeeded() }
            }
        }
        do {
            try proc.run()
            serverProcess = proc
            NSLog("Portskill: started server pid=%d", proc.processIdentifier)
        } catch {
            NSLog("Portskill: failed to start server: %@", "\(error)")
        }
    }

    private func stopServer() {
        guard let proc = serverProcess, proc.isRunning else {
            // Also clear orphaned module processes we started historically
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
            p.arguments = ["-f", "python3? -m port_registry_app"]
            try? p.run()
            p.waitUntilExit()
            return
        }
        proc.terminate()
        let deadline = Date().addingTimeInterval(3)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning { proc.interrupt() }
        serverProcess = nil
    }

    @objc func openUI() {
        NSWorkspace.shared.open(uiURL)
    }

    @objc func checkServer() {
        let ok = serverAlreadyListening()
        let alert = NSAlert()
        alert.messageText = "Portskill"
        let urlText = uiURL.absoluteString
        alert.informativeText = ok
            ? "UI+MCP server is responding at \(urlText)"
            : "Server not reachable at \(urlText) (see ~/.config/port-registry/listen.json)"
        alert.runModal()
    }

    @objc func quitAll() {
        stopping = true
        // Unload LaunchAgent so KeepAlive does not revive us
        let uid = getuid()
        for label in ["local.portskill.keepalive", "local.portskill"] {
            let boot = Process()
            boot.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            boot.arguments = ["bootout", "gui/\(uid)/\(label)"]
            try? boot.run()
            boot.waitUntilExit()
        }
        stopServer()
        // Also stop externally owned server
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        p.arguments = ["-f", "-m port_registry_app"]
        try? p.run()
        p.waitUntilExit()
        NSApp.terminate(nil)
    }
}
