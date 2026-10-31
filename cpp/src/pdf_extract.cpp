#include "pdf_extract.hpp"

#include <poppler-document.h>
#include <poppler-page.h>

#include <algorithm>
#include <cctype>
#include <memory>

namespace {

std::string utf8(const poppler::ustring& value) {
    const auto bytes = value.to_utf8();
    if (bytes.empty()) {
        return "";
    }
    return std::string(bytes.data(), bytes.size());
}

}  // namespace

ExtractedPdf extract_pdf(std::string_view bytes) {
    if (bytes.size() < 4 || bytes.substr(0, 4) != "%PDF") {
        throw PdfError("That file is not a valid PDF.");
    }
    std::unique_ptr<poppler::document> document(
        poppler::document::load_from_raw_data(bytes.data(), static_cast<int>(bytes.size())));
    if (!document) {
        throw PdfError("Could not open the PDF.");
    }
    if (document->is_locked()) {
        throw PdfError("This PDF is password protected.");
    }

    ExtractedPdf result;
    result.pages = document->pages();
    result.title = utf8(document->get_title());
    if (result.pages <= 0 || result.pages > 500) {
        throw PdfError("PDF page count is unsupported.");
    }
    for (int index = 0; index < result.pages; ++index) {
        std::unique_ptr<poppler::page> page(document->create_page(index));
        if (!page) {
            throw PdfError("Could not read a page in the PDF.");
        }
        const std::string page_text = utf8(page->text());
        if (std::all_of(page_text.begin(), page_text.end(), [](unsigned char ch) {
                return std::isspace(ch);
            })) {
            continue;
        }
        if (!result.text.empty()) {
            result.text += "\n\n";
        }
        result.text += "[page " + std::to_string(index + 1) + "]\n" + page_text;
    }
    if (result.text.empty()) {
        throw PdfError("This PDF has no extractable text. Scanned pages need OCR.");
    }
    return result;
}
