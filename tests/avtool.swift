// Test helper (not shipped): makes and inspects small movies with AVFoundation, for when ffmpeg isn't installed.
// Usage: avtool make <out.mov> <width> <height> <seconds>
//        avtool probe <file>     → JSON {duration, width, height, codec, audio}
import AVFoundation
import CoreVideo

let args = CommandLine.arguments
if args[1] == "probe" {
    let asset = AVURLAsset(url: URL(fileURLWithPath: args[2]))
    let v = asset.tracks(withMediaType: .video).first
    var codec = ""
    if let v, let fd = v.formatDescriptions.first {
        let st = CMFormatDescriptionGetMediaSubType(fd as! CMFormatDescription)
        codec = String(bytes: [24, 16, 8, 0].map { UInt8((st >> $0) & 0xff) }, encoding: .ascii) ?? ""
    }
    let size = v.map { $0.naturalSize.applying($0.preferredTransform) } ?? .zero
    let out: [String: Any] = ["duration": CMTimeGetSeconds(asset.duration), "width": abs(Int(size.width)), "height": abs(Int(size.height)),
                              "codec": codec, "audio": asset.tracks(withMediaType: .audio).count, "video": v == nil ? 0 : 1]
    print(String(data: try JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
    exit(0)
}
let url = URL(fileURLWithPath: args[2])
let w = Int(args[3])!, h = Int(args[4])!, seconds = Int(args[5])!
try? FileManager.default.removeItem(at: url)
let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h,
])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: w, kCVPixelBufferHeightKey as String: h,
])
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)
let fps = 30
for i in 0..<(fps * seconds) {
    while !input.isReadyForMoreMediaData { usleep(1000) }
    var pb: CVPixelBuffer?
    if let pool = adaptor.pixelBufferPool { CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb) }
    if pb == nil { CVPixelBufferCreate(nil, w, h, kCVPixelFormatType_32BGRA, nil, &pb) }
    let buf = pb!
    CVPixelBufferLockBaseAddress(buf, [])
    let base = CVPixelBufferGetBaseAddress(buf)!.assumingMemoryBound(to: UInt8.self)
    let row = CVPixelBufferGetBytesPerRow(buf)
    for y in 0..<h {
        for x in 0..<w {
            let p = base + y * row + x * 4
            p[0] = UInt8((x + i * 4) % 256); p[1] = UInt8(y % 256); p[2] = UInt8((i * 8) % 256); p[3] = 255
        }
    }
    CVPixelBufferUnlockBaseAddress(buf, [])
    adaptor.append(buf, withPresentationTime: CMTime(value: CMTimeValue(i), timescale: CMTimeScale(fps)))
}
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
print(writer.status == .completed ? "ok" : "failed: \(String(describing: writer.error))")
