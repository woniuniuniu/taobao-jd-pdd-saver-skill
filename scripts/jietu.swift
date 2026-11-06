// 蜗牛最爽省钱 Skill · 镜像窗口录屏小助手（jingxiang.py 第一次用到时自动编译，编译不了就退回普通截图）
//
// 为什么要它：11-05 实测，Mac 上每截一次 iPhone 镜像窗口（screencapture、CGWindowListCreateImage 都一样），
// 手机就当成自己截了一次屏，京东弹「分享截屏」、淘宝弹「找相似」、拼多多弹「把截屏分享至」，挡住下一次点击。
// 用 ScreenCaptureKit 开一路持续的录屏流、从流里取最新一帧，手机那边不会当成截屏（同一个商品页上对照实测）。
//
// 用法：启动后在标准输出打一行「ready」，然后从标准输入一行一行读命令：
//   shot <png 路径>   → 存最新一帧，回「ok <宽> <高>」或「err <原因>」
// 标准输入关掉（Python 进程结束）就退出。
import AppKit
import CoreImage
import Foundation
import ScreenCaptureKit

final class Grabber: NSObject, SCStreamOutput, SCStreamDelegate {
    private var latest: CVPixelBuffer?
    private var stamp = Date.distantPast
    private let lock = NSLock()
    var stopped: String?

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, let pb = sb.imageBuffer else { return }
        lock.lock(); latest = pb; stamp = Date(); lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stopped = "\(error)"
    }

    /// 存一帧。画面没变时流里不来新帧，最后那一帧就是现在的样子；刚点过的话等一小会儿让新帧进来。
    func save(_ path: String) -> String {
        if let s = stopped { return "err 录屏停了：\(s)" }
        let asked = Date()
        while Date().timeIntervalSince(asked) < 0.35 {
            lock.lock(); let fresh = stamp > asked; lock.unlock()
            if fresh { break }
            Thread.sleep(forTimeInterval: 0.03)
        }
        lock.lock(); let pb = latest; lock.unlock()
        guard let pb = pb else { return "err 还没有画面" }
        let ci = CIImage(cvPixelBuffer: pb)
        guard let cg = CIContext().createCGImage(ci, from: ci.extent) else { return "err 转图片失败" }
        let rep = NSBitmapImageRep(cgImage: cg)
        guard let data = rep.representation(using: .png, properties: [:]) else { return "err 转 PNG 失败" }
        do { try data.write(to: URL(fileURLWithPath: path)) } catch { return "err 写文件失败 \(error)" }
        return "ok \(cg.width) \(cg.height)"
    }
}

_ = NSApplication.shared  // 不初始化的话 CoreGraphics 会断言失败
setvbuf(stdout, nil, _IOLBF, 0)
let grabber = Grabber()
let ready = DispatchSemaphore(value: 0)
var keep: SCStream?
var failure: String?

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let win = content.windows
            .filter({ ($0.owningApplication?.bundleIdentifier ?? "") == "com.apple.ScreenContinuity" && $0.frame.width > 100 })
            .max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }) else {
            failure = "没找到 iPhone 镜像窗口"; ready.signal(); return
        }
        let cfg = SCStreamConfiguration()
        let scale = NSScreen.screens.map { $0.backingScaleFactor }.max() ?? 2
        cfg.width = Int(win.frame.width * scale)
        cfg.height = Int(win.frame.height * scale)
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 10)
        cfg.showsCursor = false
        let s = SCStream(filter: SCContentFilter(desktopIndependentWindow: win), configuration: cfg, delegate: grabber)
        try s.addStreamOutput(grabber, type: .screen, sampleHandlerQueue: DispatchQueue(label: "jietu"))
        try await s.startCapture()
        keep = s
    } catch {
        failure = "\(error)"
    }
    ready.signal()
}
ready.wait()
if let f = failure {
    print("fail \(f)")
    exit(2)
}
Thread.sleep(forTimeInterval: 0.4)  // 等第一帧
print("ready")
while let line = readLine() {
    let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
    if parts.first == "shot", parts.count == 2 {
        print(grabber.save(parts[1]))
    } else if parts.first == "quit" {
        break
    } else {
        print("err 不认识的命令")
    }
}
keep?.stopCapture { _ in exit(0) }
Thread.sleep(forTimeInterval: 0.5)
exit(0)
