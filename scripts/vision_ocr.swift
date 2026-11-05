import Foundation
import Vision
import ImageIO

// Local Apple Vision OCR. No URLSession, external service or language-model API.
guard CommandLine.arguments.count == 3 else {
    fputs("usage: vision-ocr image output.json\n", stderr)
    exit(2)
}
let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
request.minimumTextHeight = 0.001
do {
    let start = Date()
    try VNImageRequestHandler(url: imageURL, options: [:]).perform([request])
    let rows: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let text = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        // Convert Vision's bottom-left origin to top-left normalized page coordinates.
        let degrees = atan2(observation.topRight.y - observation.topLeft.y,
                            observation.topRight.x - observation.topLeft.x) * 180 / .pi
        return ["text": text.string, "confidence": text.confidence, "baseline_degrees": degrees,
                "bbox": [b.minX, 1 - b.maxY, b.maxX, 1 - b.minY]]
    }
    let data = try JSONSerialization.data(withJSONObject: [
        "engine": "Apple Vision VNRecognizeTextRequest", "revision": request.revision,
        "languages": request.recognitionLanguages, "seconds": Date().timeIntervalSince(start),
        "lines": rows
    ], options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    try data.write(to: outputURL, options: .atomic)
} catch {
    fputs("OCR failed: \(error)\n", stderr)
    exit(1)
}
