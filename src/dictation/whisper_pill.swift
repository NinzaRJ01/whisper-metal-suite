import AppKit
import AVFoundation
import CoreGraphics

// ==============================================================================
// ParakeetPill: 3-Line Multiline Streaming Dictation Card for macOS
// ==============================================================================

class PillWindow: NSWindow {
    let visualEffect = NSVisualEffectView()
    let statusLabel = NSTextField()
    
    let pillWidth: CGFloat = 480
    let pillHeight: CGFloat = 88 // Comfortably holds 3 lines of 15pt text
    
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
        self.sharingType = .none // Exclude from screen share / recordings
        
        // Dark Translucent Glass Card
        visualEffect.frame = contentRect
        visualEffect.blendingMode = .behindWindow
        visualEffect.material = .hudWindow
        visualEffect.state = .active
        visualEffect.wantsLayer = true
        visualEffect.layer?.cornerRadius = 20
        visualEffect.layer?.masksToBounds = true
        visualEffect.layer?.borderColor = NSColor.white.withAlphaComponent(0.22).cgColor
        visualEffect.layer?.borderWidth = 1.0
        
        // 3-Line Label
        statusLabel.isEditable = false
        statusLabel.isSelectable = false
        statusLabel.isBezeled = false
        statusLabel.drawsBackground = false
        statusLabel.font = NSFont.systemFont(ofSize: 15, weight: .medium)
        statusLabel.textColor = .white
        statusLabel.alignment = .left
        statusLabel.maximumNumberOfLines = 3
        statusLabel.lineBreakMode = .byWordWrapping
        statusLabel.cell?.wraps = true
        statusLabel.cell?.isScrollable = false
        statusLabel.frame = NSRect(x: 20, y: 12, width: pillWidth - 40, height: pillHeight - 24)
        
        visualEffect.addSubview(statusLabel)
        self.contentView = visualEffect
        
        centerOnScreen()
    }
    
    func centerOnScreen() {
        if let screen = NSScreen.main {
            let screenRect = screen.visibleFrame
            let x = screenRect.midX - pillWidth / 2
            let y = screenRect.minY + 90
            self.setFrame(NSRect(x: x, y: y, width: pillWidth, height: pillHeight), display: true)
        }
    }
    
    func updateLiveText(_ text: String, isFinished: Bool = false) {
        DispatchQueue.main.async {
            let icon = isFinished ? "✓ " : "🎙️ "
            
            // Format text to show the latest ~3 lines smoothly if text gets long
            let displayText = self.formatTrailingLines(prefix: icon, text: text)
            
            let paragraphStyle = NSMutableParagraphStyle()
            paragraphStyle.lineSpacing = 3
            paragraphStyle.lineBreakMode = .byWordWrapping
            
            let attrString = NSAttributedString(
                string: displayText,
                attributes: [
                    .font: NSFont.systemFont(ofSize: 15, weight: .medium),
                    .foregroundColor: NSColor.white,
                    .paragraphStyle: paragraphStyle
                ]
            )
            self.statusLabel.attributedStringValue = attrString
        }
    }
    
    // Keeps the most recent ~3 lines visible as new words stream in
    func formatTrailingLines(prefix: String, text: String) -> String {
        let full = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if full.isEmpty { return "\(prefix)Listening..." }
        
        // Approximate ~11 words per line on 440px width -> 3 lines ≈ 33 words
        let words = full.components(separatedBy: .whitespaces).filter { !$0.isEmpty }
        if words.count > 32 {
            let recentWords = words.suffix(30).joined(separator: " ")
            return "\(prefix)... \(recentWords)"
        }
        return "\(prefix)\(full)"
    }
    
    func show(text: String) {
        DispatchQueue.main.async {
            self.updateLiveText(text)
            self.centerOnScreen()
            self.alphaValue = 0.0
            self.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.12
                self.animator().alphaValue = 1.0
            }
        }
    }
    
    func hide() {
        DispatchQueue.main.async {
            NSAnimationContext.runAnimationGroup({ context in
                context.duration = 0.25
                self.animator().alphaValue = 0.0
            }, completionHandler: {
                self.orderOut(nil)
            })
        }
    }
}

class DictationController: NSObject {
    static let shared = DictationController()
    
    let pill = PillWindow()
    let audioEngine = AVAudioEngine()
    
    var isRecording = false
    var isTranscribingSlice = false
    
    var pcmLock = NSLock()
    var pcmBuffer = Data()
    
    let snapshotWavURL = URL(fileURLWithPath: "/tmp/whisper_stream_snapshot.wav")
    let finalWavURL = URL(fileURLWithPath: "/tmp/whisper_stream_final.wav")
    
    var streamTimer: DispatchSourceTimer?
    var lastTranscribedText = ""
    
    let whisperCliPath = "/opt/homebrew/bin/whisper-cli"
    
    // Dedicated English-only model for live streaming (~180ms latency, zero Russian tokens)
    let liveModelPath: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let baseEn = "\(home)/.whisper-models/ggml-base.en.bin"
        if FileManager.default.fileExists(atPath: baseEn) { return baseEn }
        let turbo = "\(home)/.whisper-models/ggml-large-v3-turbo.bin"
        return turbo
    }()
    
    // High-accuracy English-locked model for final transcription
    let finalModelPath: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let turbo = "\(home)/.whisper-models/ggml-large-v3-turbo.bin"
        if FileManager.default.fileExists(atPath: turbo) { return turbo }
        return "\(home)/.whisper-models/ggml-base.en.bin"
    }()
    
    func toggle() {
        if isRecording {
            stopAndTranscribe()
        } else {
            startRecording()
        }
    }
    
    func startRecording() {
        guard !isRecording else { return }
        isRecording = true
        lastTranscribedText = ""
        
        pcmLock.lock()
        pcmBuffer.removeAll(keepingCapacity: true)
        pcmLock.unlock()
        
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        let sampleRate = inputFormat.sampleRate
        
        let strideStep = max(1, Int(round(sampleRate / 16000.0)))
        
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] (buffer, when) in
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
            pill.show(text: "Listening...")
            
            startLiveStreamTimer()
        } catch {
            print("Failed to start audio engine: \(error)")
            isRecording = false
        }
    }
    
    func startLiveStreamTimer() {
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .userInteractive))
        timer.schedule(deadline: .now() + 0.45, repeating: 0.45)
        timer.setEventHandler { [weak self] in
            self?.decodeLiveSnapshot()
        }
        timer.resume()
        self.streamTimer = timer
    }
    
    func stopLiveStreamTimer() {
        streamTimer?.cancel()
        streamTimer = nil
    }
    
    func decodeLiveSnapshot() {
        guard isRecording, !isTranscribingSlice else { return }
        isTranscribingSlice = true
        defer { isTranscribingSlice = false }
        
        pcmLock.lock()
        let currentPcm = pcmBuffer
        pcmLock.unlock()
        
        // At least 0.4 seconds of audio (12800 bytes)
        guard currentPcm.count > 12800 else { return }
        
        let wavData = createWavData(from: currentPcm)
        do {
            try wavData.write(to: snapshotWavURL)
        } catch {
            return
        }
        
        let text = runTranscription(audioPath: snapshotWavURL.path, isFinal: false)
        if !text.isEmpty {
            lastTranscribedText = text
            // Update 3-line live display inside pill!
            pill.updateLiveText(text)
        }
    }
    
    func stopAndTranscribe() {
        guard isRecording else { return }
        isRecording = false
        
        stopLiveStreamTimer()
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        
        NSSound(named: "Pop")?.play()
        
        pcmLock.lock()
        let finalPcm = pcmBuffer
        pcmLock.unlock()
        
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            
            var text = ""
            if finalPcm.count > 6400 {
                let finalWav = self.createWavData(from: finalPcm)
                try? finalWav.write(to: self.finalWavURL)
                text = self.runTranscription(audioPath: self.finalWavURL.path, isFinal: true)
            }
            
            if text.isEmpty {
                text = self.lastTranscribedText
            }
            
            DispatchQueue.main.async {
                if !text.isEmpty {
                    self.pill.updateLiveText(text, isFinished: true)
                    
                    let pasteboard = NSPasteboard.general
                    pasteboard.clearContents()
                    pasteboard.setString(text, forType: .string)
                    
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                        self.pasteIntoFrontmostApp()
                    }
                }
                
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                    self.pill.hide()
                }
            }
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
    
    func runTranscription(audioPath: String, isFinal: Bool) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: whisperCliPath)
        
        let tmpTxt = "/tmp/whisper_pill_out_\(UUID().uuidString.prefix(6))"
        let model = isFinal ? finalModelPath : liveModelPath
        
        process.arguments = [
            "-m", model,
            "-f", audioPath,
            "-l", "en",       // ENFORCE ENGLISH ONLY (Disables Russian/auto-detection)
            "-t", "8",
            "-nt",            // No timestamps in plain text
            "-np",            // Quiet mode
            "-otxt",
            "-of", tmpTxt
        ]
        
        do {
            try process.run()
            process.waitUntilExit()
            
            let txtFile = URL(fileURLWithPath: "\(tmpTxt).txt")
            if let content = try? String(contentsOf: txtFile, encoding: .utf8) {
                try? FileManager.default.removeItem(at: txtFile)
                return content.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        } catch {
            print("Whisper execution failed: \(error)")
        }
        return ""
    }
    
    func pasteIntoFrontmostApp() {
        let src = CGEventSource(stateID: .hidSystemState)
        let vKeyCode: CGKeyCode = 9 // 'v'
        
        if let keyDown = CGEvent(keyboardEventSource: src, virtualKey: vKeyCode, keyDown: true) {
            keyDown.flags = .maskCommand
            keyDown.post(tap: .cghidEventTap)
        }
        
        if let keyUp = CGEvent(keyboardEventSource: src, virtualKey: vKeyCode, keyDown: false) {
            keyUp.flags = .maskCommand
            keyUp.post(tap: .cghidEventTap)
        }
    }
}

// Global Hotkey Listener
class AppDelegate: NSObject, NSApplicationDelegate {
    var isRightOptionDown = false
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        print("3-Line Live Streaming WhisperPill daemon started.")
        print("Language: ENGLISH ONLY (-l en).")
        print("Trigger: Hold 'Right Option' (⌥) to speak with 3-line live streaming text, release to paste.")
        
        NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            guard let self = self else { return }
            
            let isOption = event.modifierFlags.contains(.option)
            let isRight = (event.modifierFlags.rawValue & 0x40) != 0
            
            if isOption && isRight {
                if !self.isRightOptionDown {
                    self.isRightOptionDown = true
                    DictationController.shared.startRecording()
                }
            } else {
                if self.isRightOptionDown {
                    self.isRightOptionDown = false
                    DictationController.shared.stopAndTranscribe()
                }
            }
        }
        
        let sigSource = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
        sigSource.setEventHandler {
            DictationController.shared.toggle()
        }
        sigSource.resume()
        signal(SIGUSR1, SIG_IGN)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
