import Cocoa
import Vision

guard CommandLine.arguments.count >= 2 else {
    fputs("Usage: ocr <image-path>\n", stderr)
    exit(2)
}

let imagePath = CommandLine.arguments[1]
let imageURL = URL(fileURLWithPath: imagePath)

guard let image = NSImage(contentsOf: imageURL),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Could not load image: \(imagePath)\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
} catch {
    let nsError = error as NSError
    fputs("OCR failed: \(nsError.domain) \(nsError.code): \(nsError.localizedDescription)\n", stderr)
    exit(1)
}

let lines = (request.results ?? []).compactMap { observation in
    observation.topCandidates(1).first?.string
}

print(lines.joined(separator: " "))
