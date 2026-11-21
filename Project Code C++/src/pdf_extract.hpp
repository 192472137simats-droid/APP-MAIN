#pragma once

#include <stdexcept>
#include <string>
#include <string_view>

struct ExtractedPdf {
    int pages = 0;
    std::string title;
    std::string text;
};

class PdfError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

ExtractedPdf extract_pdf(std::string_view bytes);
