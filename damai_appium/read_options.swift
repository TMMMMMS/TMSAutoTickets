import Foundation
import Vision
import ImageIO

let bytes = FileHandle.standardInput.readDataToEndOfFile()
guard let source = CGImageSourceCreateWithData(bytes as CFData, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }

var values = [String]()
for region in CommandLine.arguments.dropFirst() {
    let edges = region.split(separator: ",").compactMap { Int($0) }
    guard edges.count == 4 else { exit(2) }
    let rect = CGRect(x: edges[0], y: edges[1], width: edges[2] - edges[0], height: edges[3] - edges[1])
    guard rect.width > 0, rect.height > 0, let tile = image.cropping(to: rect) else { exit(2) }
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    request.recognitionLevel = .accurate
    try VNImageRequestHandler(cgImage: tile).perform([request])
    let lines = (request.results ?? []).compactMap { observation -> String? in
        guard let best = observation.topCandidates(1).first, best.confidence >= 0.5 else { return nil }
        return best.string
    }
    values.append(lines.joined(separator: " "))
}
FileHandle.standardOutput.write(try JSONEncoder().encode(values))
