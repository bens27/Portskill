import AppKit
import Foundation
import Darwin

/// Portskill native shell: Dock icon (regular activation) + menubar status item.
/// Spawns `python3 -m port_registry_app --no-open` and keeps it alive as a child.
/// Built into macos/Portskill.app or standalone PortskillMenu.app.
@main
enum PortskillNativeMain {
    static func main() {
        let app = NSApplication.shared
        // LaunchServices discovery can miss an existing instance. Hold a kernel
        // lock for the whole event loop, shared by installed and development copies.
        let lockDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Caches/Portskill")
        do {
            try FileManager.default.createDirectory(at: lockDirectory, withIntermediateDirectories: true)
        } catch {
            NSLog("Portskill: cannot create singleton lock directory: %@", "\(error)")
            return
        }
        let lockFD = open(lockDirectory.appendingPathComponent("native.lock").path,
                          O_CREAT | O_RDWR | O_EXLOCK | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard lockFD >= 0 else {
            if errno == EWOULDBLOCK {
                DistributedNotificationCenter.default().postNotificationName(
                    NSNotification.Name("local.portskill.openUI"), object: nil, deliverImmediately: true)
            } else {
                NSLog("Portskill: cannot acquire singleton lock: %d", errno)
            }
            return
        }
        defer { close(lockFD) }
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
        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(openUI), name: NSNotification.Name("local.portskill.openUI"), object: nil)
        let external = ProcessInfo.processInfo.environment["PORTSKILL_SERVER_EXTERNAL"] == "1"
        if !external {
            startServerIfNeeded()
        } else {
            NSLog("Portskill: external server mode (LaunchAgent owns python)")
        }
        // Open browser once on interactive double-click (not LaunchAgent)
        if ProcessInfo.processInfo.environment["PORTSKILL_KEEPALIVE"] != "1",
           ProcessInfo.processInfo.environment["PORTSKILL_NO_OPEN"] != "1" {
            openUI()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openUI()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        shutdown()
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
        guard serverProcess?.isRunning != true else { return }
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
        // Drain child output continuously; an unread pipe eventually blocks the server.
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
            } else if let text = String(data: data, encoding: .utf8) {
                NSLog("Portskill server: %@", text)
            }
        }
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
        guard let proc = serverProcess, proc.isRunning else { return }
        proc.terminate()
        let deadline = Date().addingTimeInterval(3)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning { proc.interrupt() }
        serverProcess = nil
    }

    @objc func openUI() {
        if ProcessInfo.processInfo.environment["PORTSKILL_SERVER_EXTERNAL"] != "1" {
            startServerIfNeeded()
        }
        openWhenReady(attempts: 30)
    }

    private func openWhenReady(attempts: Int) {
        if serverAlreadyListening() {
            NSWorkspace.shared.open(uiURL)
        } else if attempts > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
                self?.openWhenReady(attempts: attempts - 1)
            }
        } else {
            checkServer()
        }
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
        NSApp.terminate(nil)
    }

    private func shutdown() {
        guard !stopping else { return }
        stopping = true
        DistributedNotificationCenter.default().removeObserver(self)
        // A supervisor-launched native shell can itself die during bootout.
        // Signal its recorded server first, then unload before the restart delay.
        stopRecordedServer()
        let uid = getuid()
        for label in ["local.portskill.keepalive", "local.portskill"] {
            let boot = Process()
            boot.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            boot.arguments = ["bootout", "gui/\(uid)/\(label)"]
            do {
                try boot.run()
                boot.waitUntilExit()
            } catch { NSLog("Portskill: could not stop supervisor: %@", "\(error)") }
        }
        stopServer()
    }

    private func stopRecordedServer() {
        // A LaunchAgent-owned server is not our child. Target the recorded PID,
        // and verify its command before signalling it; never pkill all Python/MCP sessions.
        let path = NSString(string: "~/.config/port-registry/listen.json").expandingTildeInPath
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let pid = obj["pid"] as? Int32, pid > 1 else { return }
        let check = Process()
        let output = Pipe()
        check.executableURL = URL(fileURLWithPath: "/bin/ps")
        check.arguments = ["-p", String(pid), "-o", "uid=,command="]
        check.standardOutput = output
        do {
            try check.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            check.waitUntilExit()
            let command = String(data: data, encoding: .utf8) ?? ""
            let fields = command.split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
            guard fields.count == 2, fields[0] == String(getuid()),
                  fields[1].contains("-m port_registry_app"),
                  !fields[1].contains("--mcp-stdio") else { return }
            kill(pid, SIGTERM)
        } catch { NSLog("Portskill: could not inspect server: %@", "\(error)") }
    }
}
