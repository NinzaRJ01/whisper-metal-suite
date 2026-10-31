import AppKit
import AVFoundation
import CoreGraphics
import Foundation

// ==============================================================================
// MeetingRecorder: 1-Click / Hotkey (⌥ /) Meeting Audio Recorder & Summarizer
// Automatically transcribes via Whisper Metal and opens summary in browser.
// ==============================================================================

class FloatingStatusPill: NSWindow {
    let visualEffect = NSVisualEffectView()
    let statusLabel = NSTextField()
    let pillWidth: CGFloat = 460
    let pillHeight: CGFloat = 64
    
    init() {
        let contentRect = NSRect(x: 0, y: 0, width: pillWidth, height: pillHeight)
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        self.isOpaque = false
        self.backgroundColor = .clear
        self.level = .floating
        self.ignoresMouseEvents = true
        self.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        self.sharingType = .none // Exclude from Zoom, Teams, Google Meet & ScreenCaptureKit
        
        // Translucent dark glass
        visualEffect.frame = contentRect
        visualEffect.blendingMode = .behindWindow
        visualEffect.material = .hudWindow
        visualEffect.state = .active
        visualEffect.wantsLayer = true
        visualEffect.layer?.cornerRadius = 18
        visualEffect.layer?.masksToBounds = true
        visualEffect.layer?.borderColor = NSColor.white.withAlphaComponent(0.2).cgColor
        visualEffect.layer?.borderWidth = 1.0
        
        // Status text label
        statusLabel.isEditable = false
        statusLabel.isSelectable = false
        statusLabel.isBezeled = false
        statusLabel.drawsBackground = false
        statusLabel.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        statusLabel.textColor = .white
        statusLabel.alignment = .center
        statusLabel.frame = NSRect(x: 16, y: 16, width: pillWidth - 32, height: 32)
        
        visualEffect.addSubview(statusLabel)
        self.contentView = visualEffect
        centerTop()
    }
    
    func centerTop() {
        if let screen = NSScreen.main {
            let screenRect = screen.visibleFrame
            let x = screenRect.midX - pillWidth / 2
            let y = screenRect.maxY - pillHeight - 24
            self.setFrame(NSRect(x: x, y: y, width: pillWidth, height: pillHeight), display: true)
        }
    }
    
    func show(text: String, isRecording: Bool = false) {
        DispatchQueue.main.async {
            self.updateText(text, isRecording: isRecording)
            self.centerTop()
            self.alphaValue = 0.0
            self.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.15
                self.animator().alphaValue = 1.0
            }
        }
    }
    
    func updateText(_ text: String, isRecording: Bool = false) {
        DispatchQueue.main.async {
            let color = isRecording ? NSColor(calibratedRed: 1.0, green: 0.35, blue: 0.35, alpha: 1.0) : .white
            let attr = NSAttributedString(
                string: text,
                attributes: [
                    .font: NSFont.systemFont(ofSize: 15, weight: .semibold),
                    .foregroundColor: color
                ]
            )
            self.statusLabel.attributedStringValue = attr
        }
    }
    
    func hide(after delay: TimeInterval = 0.0) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.25
                self.animator().alphaValue = 0.0
            }, completionHandler: {
                self.orderOut(nil)
            })
        }
    }
}

class MeetingManager: NSObject {
    static let shared = MeetingManager()
    
    let pill = FloatingStatusPill()
    let audioEngine = AVAudioEngine()
    var statusItem: NSStatusItem?
    
    var stealthMode: Bool = CommandLine.arguments.contains("--stealth") || CommandLine.arguments.contains("--no-hud") || CommandLine.arguments.contains("-s")
    
    var isRecording = false
    var recordingTimer: Timer?
    var elapsedSeconds = 0
    
    var currentMeetingDir: URL?
    var currentWavURL: URL?
    
    var pcmLock = NSLock()
    var pcmBuffer = Data()
    
    let baseMeetingsDir: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let dir = home.appendingPathComponent("EmptyOne/meetings")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()
    
    let whisperModelPath: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let turbo = "\(home)/.whisper-models/ggml-large-v3-turbo.bin"
        let base = "\(home)/.whisper-models/ggml-base.en.bin"
        if FileManager.default.fileExists(atPath: turbo) { return turbo }
        return base
    }()
    
    func toggle() {
        if isRecording {
            stopAndSummarize()
        } else {
            startRecording()
        }
    }
    
    func toggleStealth() {
        stealthMode = !stealthMode
        if stealthMode {
            pill.hide()
            print("Stealth Mode: ON (HUD suppressed, screen-share safe)")
        } else {
            print("Stealth Mode: OFF (HUD visible)")
        }
        if let del = NSApplication.shared.delegate as? AppDelegate {
            del.updateStealthMenuState()
        }
    }
    
    func startRecording() {
        guard !isRecording else { return }
        isRecording = true
        elapsedSeconds = 0
        
        pcmLock.lock()
        pcmBuffer.removeAll(keepingCapacity: true)
        pcmLock.unlock()
        
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let folderName = "Meeting_\(formatter.string(from: Date()))"
        let meetingDir = baseMeetingsDir.appendingPathComponent(folderName)
        try? FileManager.default.createDirectory(at: meetingDir, withIntermediateDirectories: true)
        
        self.currentMeetingDir = meetingDir
        self.currentWavURL = meetingDir.appendingPathComponent("audio.wav")
        
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        let sampleRate = inputFormat.sampleRate
        let strideStep = max(1, Int(round(sampleRate / 16000.0)))
        
        inputNode.installTap(onBus: 0, bufferSize: 2048, format: inputFormat) { [weak self] (buffer, when) in
            guard let self = self, self.isRecording else { return }
            guard let channelData = buffer.floatChannelData?[0] else { return }
            let frameCount = Int(buffer.frameLength)
            
            var chunk = Data(capacity: (frameCount / strideStep) * 2)
            for i in stride(from: 0, to: frameCount, by: strideStep) {
                let sample = max(-1.0, min(1.0, channelData[i]))
                let int16 = Int16(sample * 32767.0)
                var little = int16.littleEndian
                withUnsafeBytes(of: &little) { chunk.append(contentsOf: $0) }
            }
            
            self.pcmLock.lock()
            self.pcmBuffer.append(chunk)
            self.pcmLock.unlock()
        }
        
        do {
            try audioEngine.start()
            NSSound(named: "Tink")?.play()
            
            if !stealthMode {
                pill.show(text: "🔴 Recording Meeting... [00:00]", isRecording: true)
            }
            updateStatusItem(title: "🔴 00:00")
            
            DispatchQueue.main.async {
                self.recordingTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
                    guard let self = self, self.isRecording else { return }
                    self.elapsedSeconds += 1
                    let mins = self.elapsedSeconds / 60
                    let secs = self.elapsedSeconds % 60
                    let timeStr = String(format: "%02d:%02d", mins, secs)
                    
                    if !self.stealthMode {
                        self.pill.updateText("🔴 Recording Meeting... [\(timeStr)]", isRecording: true)
                    }
                    self.updateStatusItem(title: "🔴 \(timeStr)")
                }
            }
        } catch {
            print("Audio engine failed: \(error)")
            isRecording = false
        }
    }
    
    func stopAndSummarize() {
        guard isRecording else { return }
        isRecording = false
        
        recordingTimer?.invalidate()
        recordingTimer = nil
        
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        NSSound(named: "Pop")?.play()
        
        if !stealthMode {
            pill.updateText("⏳ Transcribing with Whisper Metal...", isRecording: false)
        }
        updateStatusItem(title: "⏳ Transcribing...")
        
        pcmLock.lock()
        let recordedPcm = pcmBuffer
        pcmLock.unlock()
        
        guard let wavURL = currentWavURL, let meetingDir = currentMeetingDir else { return }
        
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            
            // 1. Write WAV File
            let wavData = self.createWavData(from: recordedPcm)
            try? wavData.write(to: wavURL)
            
            // 2. Transcribe via whisper-cli
            let transcriptBase = meetingDir.appendingPathComponent("transcript").path
            let whisperCli = "/opt/homebrew/bin/whisper-cli"
            
            let whisperProc = Process()
            whisperProc.executableURL = URL(fileURLWithPath: whisperCli)
            whisperProc.arguments = [
                "-m", self.whisperModelPath,
                "-f", wavURL.path,
                "-l", "en", // Enforce English only (prevents accidental Russian auto-detection)
                "-otxt",
                "-oj",
                "-of", transcriptBase,
                "-t", "8",
                "-np"
            ]
            
            do {
                try whisperProc.run()
                whisperProc.waitUntilExit()
            } catch {
                print("Whisper execution error: \(error)")
            }
            
            let txtFile = meetingDir.appendingPathComponent("transcript.txt")
            if !FileManager.default.fileExists(atPath: txtFile.path) {
                // If whisper didn't output text (e.g. silent audio), write placeholder
                try? "Meeting recording completed. No audible speech detected.".write(to: txtFile, atomically: true, encoding: .utf8)
            }
            
            // 3. Summarize & Generate HTML
            DispatchQueue.main.async {
                if !self.stealthMode {
                    self.pill.updateText("🧠 Generating Executive Summary...", isRecording: false)
                }
                self.updateStatusItem(title: "🧠 Summarizing...")
            }
            
            let summaryProc = Process()
            summaryProc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            let summarizerScript = "/Users/test/EmptyOne/summarize_meeting.py"
            summaryProc.arguments = [summarizerScript, txtFile.path, wavURL.path]
            
            do {
                try summaryProc.run()
                summaryProc.waitUntilExit()
            } catch {
                print("Summarizer error: \(error)")
            }
            
            // 4. Open in default browser (bypassed in stealth mode so screen-sharing is never interrupted!)
            let htmlURL = meetingDir.appendingPathComponent("summary.html")
            DispatchQueue.main.async {
                if !self.stealthMode {
                    self.pill.updateText("✓ Meeting Summary Ready! Opening in browser...", isRecording: false)
                    self.updateStatusItem(title: "🎙️")
                    
                    if FileManager.default.fileExists(atPath: htmlURL.path) {
                        NSWorkspace.shared.open(htmlURL)
                    }
                    self.pill.hide(after: 2.5)
                } else {
                    self.updateStatusItem(title: "🎙️")
                    // Screen-share safe notification
                    let notifyProc = Process()
                    notifyProc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                    notifyProc.arguments = ["-e", "display notification \"Meeting summary ready in EmptyOne/meetings/\(meetingDir.lastPathComponent)\" with title \"MeetingRecorder (Stealth)\""]
                    try? notifyProc.run()
                }
            }
        }
    }
    
    func updateStatusItem(title: String) {
        DispatchQueue.main.async {
            self.statusItem?.button?.title = title
        }
    }
    
    func createWavData(from pcm: Data) -> Data {
        var data = Data()
        let sampleRate: Int32 = 16000
        let channels: Int16 = 1
        let bitsPerSample: Int16 = 16
        let byteRate = sampleRate * Int32(channels) * Int32(bitsPerSample / 8)
        let blockAlign = channels * (bitsPerSample / 8)
        let subchunk2Size = Int32(pcm.count)
        let chunkSize = 36 + subchunk2Size

        data.append("RIFF".data(using: .ascii)!)
        data.append(withUnsafeBytes(of: chunkSize.littleEndian) { Data($0) })
        data.append("WAVE".data(using: .ascii)!)
        data.append("fmt ".data(using: .ascii)!)
        let subchunk1Size: Int32 = 16
        data.append(withUnsafeBytes(of: subchunk1Size.littleEndian) { Data($0) })
        let audioFormat: Int16 = 1
        data.append(withUnsafeBytes(of: audioFormat.littleEndian) { Data($0) })
        data.append(withUnsafeBytes(of: channels.littleEndian) { Data($0) })
        data.append(withUnsafeBytes(of: sampleRate.littleEndian) { Data($0) })
        data.append(withUnsafeBytes(of: byteRate.littleEndian) { Data($0) })
        data.append(withUnsafeBytes(of: blockAlign.littleEndian) { Data($0) })
        data.append(withUnsafeBytes(of: bitsPerSample.littleEndian) { Data($0) })
        data.append("data".data(using: .ascii)!)
        data.append(withUnsafeBytes(of: subchunk2Size.littleEndian) { Data($0) })
        data.append(pcm)
        return data
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var sigSource: DispatchSourceSignal?
    var sigusr2Source: DispatchSourceSignal?
    var stealthMenuItem: NSMenuItem?
    var lastOptionTime: Date = Date.distantPast
    var wasOptionDown = false
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        print("MeetingRecorder daemon initialized.")
        print("Trigger: Double-tap Option (⌥ ⌥) anywhere to Start/Stop meeting recording.")
        if MeetingManager.shared.stealthMode {
            print("Mode: STEALTH (HUD completely hidden, screen-share safe).")
        }
        
        // Setup Menu Bar Item
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "🎙️"
        
        let menu = NSMenu()
        let toggleItem = NSMenuItem(title: "Record / Stop Meeting (⌥ ⌥)", action: #selector(toggleRecording), keyEquivalent: "")
        toggleItem.target = self
        menu.addItem(toggleItem)
        
        let stealthItem = NSMenuItem(title: "Stealth Mode (Screen Share Safe)", action: #selector(toggleStealthFromMenu), keyEquivalent: "")
        stealthItem.target = self
        stealthItem.state = MeetingManager.shared.stealthMode ? .on : .off
        self.stealthMenuItem = stealthItem
        menu.addItem(stealthItem)
        
        menu.addItem(NSMenuItem.separator())
        
        let openFolderItem = NSMenuItem(title: "Open Meetings Folder", action: #selector(openMeetingsFolder), keyEquivalent: "")
        openFolderItem.target = self
        menu.addItem(openFolderItem)
        
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit MeetingRecorder", action: #selector(quitApp), keyEquivalent: "q"))
        
        statusItem.menu = menu
        MeetingManager.shared.statusItem = statusItem
        
        // 1. Double-Tap Option Monitor (⌥ ⌥)
        NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            guard let self = self else { return }
            let isOptionDown = event.modifierFlags.contains(.option)
            if isOptionDown && !self.wasOptionDown {
                let now = Date()
                let interval = now.timeIntervalSince(self.lastOptionTime)
                if interval < 0.45 && interval > 0.04 {
                    MeetingManager.shared.toggle()
                    self.lastOptionTime = Date.distantPast
                } else {
                    self.lastOptionTime = now
                }
            }
            self.wasOptionDown = isOptionDown
        }
        
        NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            guard let self = self else { return event }
            let isOptionDown = event.modifierFlags.contains(.option)
            if isOptionDown && !self.wasOptionDown {
                let now = Date()
                let interval = now.timeIntervalSince(self.lastOptionTime)
                if interval < 0.45 && interval > 0.04 {
                    MeetingManager.shared.toggle()
                    self.lastOptionTime = Date.distantPast
                } else {
                    self.lastOptionTime = now
                }
            }
            self.wasOptionDown = isOptionDown
            return event
        }
        
        // 2. Also keep Option + / as fallback
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self = self else { return }
            let isOption = event.modifierFlags.contains(.option)
            if isOption && event.keyCode == 44 { // 44 is '/' key
                MeetingManager.shared.toggle()
            } else {
                // Any other key pressed cancels pending double-tap
                self.lastOptionTime = Date.distantPast
            }
        }
        
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self = self else { return event }
            let isOption = event.modifierFlags.contains(.option)
            if isOption && event.keyCode == 44 {
                MeetingManager.shared.toggle()
                return nil
            } else {
                self.lastOptionTime = Date.distantPast
            }
            return event
        }
        
        // SIGUSR1 for CLI toggling
        let source = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
        source.setEventHandler {
            MeetingManager.shared.toggle()
        }
        source.resume()
        self.sigSource = source
        signal(SIGUSR1, SIG_IGN)
        
        // SIGUSR2 for CLI stealth toggle
        let sig2 = DispatchSource.makeSignalSource(signal: SIGUSR2, queue: .main)
        sig2.setEventHandler {
            MeetingManager.shared.toggleStealth()
        }
        sig2.resume()
        self.sigusr2Source = sig2
        signal(SIGUSR2, SIG_IGN)
    }
    
    @objc func toggleRecording() {
        MeetingManager.shared.toggle()
    }
    
    @objc func toggleStealthFromMenu() {
        MeetingManager.shared.toggleStealth()
    }
    
    func updateStealthMenuState() {
        DispatchQueue.main.async {
            self.stealthMenuItem?.state = MeetingManager.shared.stealthMode ? .on : .off
        }
    }
    
    @objc func openMeetingsFolder() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let dir = home.appendingPathComponent("EmptyOne/meetings")
        NSWorkspace.shared.open(dir)
    }
    
    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
