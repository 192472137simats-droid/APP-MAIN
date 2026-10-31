#include "analysis.hpp"
#include "paper_store.hpp"
#include "pdf_extract.hpp"

#include <array>
#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
using socket_t = SOCKET;
constexpr socket_t invalid_socket = INVALID_SOCKET;
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
using socket_t = int;
constexpr socket_t invalid_socket = -1;
#endif

namespace {

constexpr std::size_t max_pdf_bytes = 25 * 1024 * 1024;
constexpr std::size_t max_header_bytes = 16 * 1024;
constexpr std::size_t max_request_bytes = max_pdf_bytes + 64 * 1024;

struct HttpError : std::runtime_error {
    int status;
    HttpError(int status_code, const std::string& message)
        : std::runtime_error(message), status(status_code) {}
};

struct HttpRequest {
    std::string method;
    std::string target;
    std::map<std::string, std::string> headers;
    std::string body;
};

struct UploadedFile {
    std::string filename;
    std::string bytes;
};

void close_socket(socket_t socket) {
#ifdef _WIN32
    closesocket(socket);
#else
    close(socket);
#endif
}

class SocketRuntime {
public:
    SocketRuntime() {
#ifdef _WIN32
        WSADATA data{};
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
            throw std::runtime_error("WSAStartup failed");
        }
#endif
    }

    ~SocketRuntime() {
#ifdef _WIN32
        WSACleanup();
#endif
    }
};

void set_receive_timeout(socket_t socket) {
#ifdef _WIN32
    const DWORD timeout_ms = 10000;
    setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO,
               reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms));
#else
    timeval timeout{10, 0};
    setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
#endif
}

bool send_all(socket_t client, std::string_view data) {
    std::size_t sent = 0;
    while (sent < data.size()) {
        const int count = send(client, data.data() + sent,
                               static_cast<int>(data.size() - sent), 0);
        if (count <= 0) {
            return false;
        }
        sent += static_cast<std::size_t>(count);
    }
    return true;
}

const char* reason(int status) {
    switch (status) {
        case 200: return "OK";
        case 202: return "Accepted";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 405: return "Method Not Allowed";
        case 409: return "Conflict";
        case 413: return "Payload Too Large";
        case 415: return "Unsupported Media Type";
        case 422: return "Unprocessable Content";
        case 431: return "Request Header Fields Too Large";
        case 502: return "Bad Gateway";
        case 503: return "Service Unavailable";
        default: return "Internal Server Error";
    }
}

void respond(socket_t client, int status, std::string_view content_type,
             std::string_view body) {
    const std::string headers =
        "HTTP/1.1 " + std::to_string(status) + " " + reason(status) + "\r\n" +
        "Content-Type: " + std::string(content_type) + "\r\n" +
        "Content-Length: " + std::to_string(body.size()) + "\r\n" +
        "Connection: close\r\n" +
        "X-Content-Type-Options: nosniff\r\n\r\n";
    if (send_all(client, headers)) {
        send_all(client, body);
    }
}

std::string lower(std::string value) {
    for (char& ch : value) {
        if (ch >= 'A' && ch <= 'Z') {
            ch = static_cast<char>(ch - 'A' + 'a');
        }
    }
    return value;
}

std::string trim(std::string_view value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string_view::npos) {
        return "";
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    return std::string(value.substr(first, last - first + 1));
}

HttpRequest read_request(socket_t client) {
    std::string received;
    std::array<char, 8192> buffer{};
    std::size_t header_end = std::string::npos;
    while (header_end == std::string::npos) {
        const int count = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
        if (count <= 0) {
            throw HttpError(400, "Incomplete request headers.");
        }
        received.append(buffer.data(), static_cast<std::size_t>(count));
        header_end = received.find("\r\n\r\n");
        if (header_end == std::string::npos && received.size() > max_header_bytes) {
            throw HttpError(431, "Request headers are too large.");
        }
    }
    if (header_end > max_header_bytes) {
        throw HttpError(431, "Request headers are too large.");
    }

    HttpRequest request;
    const auto line_end = received.find("\r\n");
    const std::string_view line(received.data(), line_end);
    const auto first_space = line.find(' ');
    const auto second_space = first_space == std::string_view::npos
        ? std::string_view::npos : line.find(' ', first_space + 1);
    if (second_space == std::string_view::npos ||
        line.substr(second_space + 1, 5) != "HTTP/") {
        throw HttpError(400, "Invalid request line.");
    }
    request.method = std::string(line.substr(0, first_space));
    request.target = std::string(line.substr(first_space + 1,
                                             second_space - first_space - 1));

    std::size_t cursor = line_end + 2;
    while (cursor < header_end) {
        const auto end = received.find("\r\n", cursor);
        if (end == std::string::npos || end > header_end) {
            throw HttpError(400, "Invalid request headers.");
        }
        const std::string_view header(received.data() + cursor, end - cursor);
        const auto colon = header.find(':');
        if (colon == std::string_view::npos) {
            throw HttpError(400, "Invalid request header.");
        }
        request.headers[lower(trim(header.substr(0, colon)))] =
            trim(header.substr(colon + 1));
        cursor = end + 2;
    }

    std::size_t content_length = 0;
    const auto length_header = request.headers.find("content-length");
    if (length_header != request.headers.end()) {
        const std::string& value = length_header->second;
        const auto parsed = std::from_chars(value.data(), value.data() + value.size(),
                                            content_length);
        if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
            throw HttpError(400, "Invalid Content-Length.");
        }
    }
    if (content_length > max_request_bytes) {
        throw HttpError(413, "Upload exceeds the 25 MB PDF limit.");
    }
    const std::size_t body_start = header_end + 4;
    while (received.size() - body_start < content_length) {
        const int count = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
        if (count <= 0) {
            throw HttpError(400, "Incomplete request body.");
        }
        received.append(buffer.data(), static_cast<std::size_t>(count));
    }
    request.body.assign(received.data() + body_start, content_length);
    return request;
}

std::string json_string(std::string_view text) {
    constexpr char hex[] = "0123456789abcdef";
    std::string out = "\"";
    for (unsigned char ch : text) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (ch < 0x20) {
                    out += "\\u00";
                    out += hex[ch >> 4];
                    out += hex[ch & 0x0f];
                } else {
                    out += static_cast<char>(ch);
                }
        }
    }
    out += '"';
    return out;
}

void respond_error(socket_t client, int status, const std::string& detail) {
    respond(client, status, "application/json; charset=utf-8",
            "{\"detail\":" + json_string(detail) + "}");
}

UploadedFile parse_upload(const HttpRequest& request) {
    const auto content_type = request.headers.find("content-type");
    if (content_type == request.headers.end() ||
        lower(content_type->second).find("multipart/form-data") != 0) {
        throw HttpError(415, "Upload must be multipart/form-data.");
    }
    const auto marker = content_type->second.find("boundary=");
    if (marker == std::string::npos) {
        throw HttpError(400, "Missing multipart boundary.");
    }
    std::string boundary = trim(std::string_view(content_type->second).substr(marker + 9));
    const auto semicolon = boundary.find(';');
    if (semicolon != std::string::npos) {
        boundary.resize(semicolon);
    }
    if (boundary.size() >= 2 && boundary.front() == '"' && boundary.back() == '"') {
        boundary = boundary.substr(1, boundary.size() - 2);
    }
    if (boundary.empty() || boundary.size() > 70 ||
        boundary.find_first_of("\r\n") != std::string::npos) {
        throw HttpError(400, "Invalid multipart boundary.");
    }

    const std::string opening = "--" + boundary + "\r\n";
    if (request.body.compare(0, opening.size(), opening) != 0) {
        throw HttpError(400, "Invalid multipart body.");
    }
    const auto part_end = request.body.find("\r\n\r\n", opening.size());
    if (part_end == std::string::npos || part_end > 8192) {
        throw HttpError(400, "Invalid file part headers.");
    }
    const std::string_view part_headers(request.body.data() + opening.size(),
                                        part_end - opening.size());
    if (part_headers.find("name=\"file\"") == std::string_view::npos) {
        throw HttpError(400, "Expected a file field named 'file'.");
    }
    const auto name_start = part_headers.find("filename=\"");
    if (name_start == std::string_view::npos) {
        throw HttpError(400, "Missing PDF filename.");
    }
    const auto filename_begin = name_start + 10;
    const auto filename_end = part_headers.find('"', filename_begin);
    if (filename_end == std::string_view::npos) {
        throw HttpError(400, "Invalid PDF filename.");
    }
    std::string filename(part_headers.substr(filename_begin,
                                             filename_end - filename_begin));
    const auto separator = filename.find_last_of("\\/");
    if (separator != std::string::npos) {
        filename.erase(0, separator + 1);
    }
    if (filename.empty() || filename.size() > 255 ||
        lower(filename).substr(filename.size() >= 4 ? filename.size() - 4 : 0) != ".pdf") {
        throw HttpError(400, "Only PDF files are supported.");
    }

    const auto file_start = part_end + 4;
    const std::string closing = "\r\n--" + boundary;
    const auto file_end = request.body.find(closing, file_start);
    if (file_end == std::string::npos ||
        request.body.compare(file_end + closing.size(), 2, "--") != 0) {
        throw HttpError(400, "Invalid multipart ending.");
    }
    const auto size = file_end - file_start;
    if (size > max_pdf_bytes) {
        throw HttpError(413, "PDF exceeds the 25 MB limit.");
    }
    return {filename, request.body.substr(file_start, size)};
}

std::size_t utf8_characters(std::string_view text) {
    std::size_t count = 0;
    for (unsigned char ch : text) {
        if ((ch & 0xc0) != 0x80) {
            ++count;
        }
    }
    return count;
}

std::string random_id() {
    static std::random_device device;
    constexpr char hex[] = "0123456789abcdef";
    std::uniform_int_distribution<int> digit(0, 15);
    std::string id;
    for (int i = 0; i < 12; ++i) {
        id += hex[digit(device)];
    }
    return id;
}

std::string paper_json(const PaperRecord& paper, bool include_text) {
    std::string json =
        "{\"id\":" + json_string(paper.id) +
        ",\"filename\":" + json_string(paper.filename) +
        ",\"title\":" + json_string(paper.title) +
        ",\"pages\":" + std::to_string(paper.pages) +
        ",\"characters\":" + std::to_string(paper.characters) +
        ",\"created_at\":" + json_string(paper.created_at) +
        ",\"analysis\":" + (paper.analysis_json.empty() ? "null" : paper.analysis_json) +
        ",\"grounding\":null,\"analysis_status\":" + json_string(paper.analysis_status) +
        ",\"analysis_error\":" + json_string(paper.analysis_error);
    if (include_text) {
        json += ",\"full_text\":" + json_string(paper.full_text);
    }
    json += "}";
    return json;
}

void serve_asset(socket_t client, const std::filesystem::path& static_dir,
                 const std::string& path) {
    std::string_view filename;
    std::string_view content_type;
    if (path == "/" || path == "/index.html") {
        filename = "index.html";
        content_type = "text/html; charset=utf-8";
    } else if (path == "/styles.css") {
        filename = "styles.css";
        content_type = "text/css; charset=utf-8";
    } else if (path == "/app.js") {
        filename = "app.js";
        content_type = "text/javascript; charset=utf-8";
    } else {
        throw HttpError(404, "Not found.");
    }
    std::ifstream file(static_dir / filename, std::ios::binary);
    if (!file) {
        throw std::runtime_error("Web asset is missing");
    }
    const std::string body(std::istreambuf_iterator<char>{file},
                           std::istreambuf_iterator<char>{});
    respond(client, 200, content_type, body);
}

void serve(socket_t client, const std::filesystem::path& static_dir,
           PaperStore& store, AnalysisWorker& worker) {
    const HttpRequest request = read_request(client);
    if (request.method == "POST" && request.target == "/api/core/analyze") {
        if (!worker.configured()) {
            throw HttpError(503, "Set LLM_API_KEY and LLM_MODEL to enable analysis.");
        }
        if (request.body.empty()) {
            throw HttpError(400, "Paper text is required.");
        }
        try {
            const std::string analysis = worker.analyze_text(request.body);
            respond(client, 200, "application/json; charset=utf-8", analysis);
        } catch (const std::exception& error) {
            throw HttpError(502, error.what());
        }
        return;
    }
    if (request.method == "GET" && request.target == "/api/health") {
        respond(client, 200, "application/json; charset=utf-8",
                "{\"status\":\"ok\",\"service\":\"research-advisor-cpp\","
                "\"analysis_configured\":" + std::string(worker.configured() ? "true" : "false") +
                ",\"model\":" + json_string(worker.model()) + "}");
        return;
    }
    if (request.method == "POST" && request.target == "/api/upload") {
        const auto upload = parse_upload(request);
        const auto pdf = extract_pdf(upload.bytes);
        PaperRecord paper;
        paper.id = random_id();
        paper.filename = upload.filename;
        paper.title = pdf.title.empty() ? upload.filename : pdf.title;
        paper.pages = pdf.pages;
        paper.characters = utf8_characters(pdf.text);
        paper.full_text = pdf.text;
        store.save(paper);
        const std::string json =
            "{\"paper_id\":" + json_string(paper.id) +
            ",\"filename\":" + json_string(paper.filename) +
            ",\"pages\":" + std::to_string(paper.pages) +
            ",\"characters\":" + std::to_string(paper.characters) +
            ",\"sections_detected\":[],\"warnings\":[],\"source\":\"text-layer\""
            ",\"analysis\":null,\"analysis_status\":\"extracted\"}";
        respond(client, 200, "application/json; charset=utf-8", json);
        return;
    }
    if (request.method == "GET" && request.target == "/api/papers") {
        std::string json = "[";
        for (const auto& paper : store.list()) {
            if (json.size() > 1) {
                json += ',';
            }
            json += paper_json(paper, false);
        }
        json += ']';
        respond(client, 200, "application/json; charset=utf-8", json);
        return;
    }
    constexpr std::string_view papers_prefix = "/api/papers/";
    constexpr std::string_view analyze_suffix = "/analyze";
    if (request.method == "POST" &&
        request.target.rfind(papers_prefix, 0) == 0 &&
        request.target.size() > papers_prefix.size() + analyze_suffix.size() &&
        request.target.compare(request.target.size() - analyze_suffix.size(),
                               analyze_suffix.size(), analyze_suffix) == 0) {
        const std::string id = request.target.substr(
            papers_prefix.size(),
            request.target.size() - papers_prefix.size() - analyze_suffix.size());
        if (!store.get(id)) throw HttpError(404, "Paper not found.");
        if (!worker.configured()) {
            throw HttpError(503, "Set LLM_API_KEY and LLM_MODEL to enable analysis.");
        }
        if (!store.mark_analysis_pending(id)) {
            throw HttpError(409, "This paper is already being analysed.");
        }
        worker.enqueue(id);
        respond(client, 202, "application/json; charset=utf-8",
                "{\"paper_id\":" + json_string(id) + ",\"analysis_status\":\"pending\"}");
        return;
    }
    if (request.method == "GET" && request.target.rfind("/api/papers/", 0) == 0) {
        const auto question = request.target.find('?');
        const std::string id = request.target.substr(12, question == std::string::npos
                                                     ? std::string::npos : question - 12);
        const auto paper = store.get(id);
        if (!paper) {
            throw HttpError(404, "Paper not found.");
        }
        const bool include_text = question != std::string::npos &&
            request.target.substr(question + 1) == "include_text=true";
        respond(client, 200, "application/json; charset=utf-8",
                paper_json(*paper, include_text));
        return;
    }
    if (request.method != "GET") {
        throw HttpError(405, "Method not allowed.");
    }
    serve_asset(client, static_dir, request.target);
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cerr << "Usage: research-advisor-server <port> <static-dir>\n";
        return 2;
    }
    try {
        std::size_t parsed = 0;
        const auto port = std::stoul(argv[1], &parsed);
        if (parsed != std::string(argv[1]).size() || port == 0 || port > 65535) {
            throw std::invalid_argument("port must be between 1 and 65535");
        }
        const std::filesystem::path static_dir(argv[2]);
        if (!std::filesystem::is_directory(static_dir)) {
            throw std::invalid_argument("static directory does not exist");
        }
        const char* data_env = std::getenv("RESEARCH_ADVISOR_DATA_DIR");
        const std::filesystem::path data_dir = data_env && *data_env ? data_env : "data";
        const auto database_path = data_dir / "researchadvisor-cpp.db";
        PaperStore store(database_path);
        store.reap_stalled_analyses();
        AnalysisWorker worker(database_path, LlmConfig::load(".env"));
        SocketRuntime runtime;
        const socket_t listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener == invalid_socket) {
            throw std::runtime_error("could not create socket");
        }
        const char reuse = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        address.sin_port = htons(static_cast<std::uint16_t>(port));
        if (bind(listener, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0 ||
            listen(listener, 16) != 0) {
            close_socket(listener);
            throw std::runtime_error("could not bind or listen on the requested port");
        }
        std::cout << "Serving http://127.0.0.1:" << port << "\n" << std::flush;
        for (;;) {
            const socket_t client = accept(listener, nullptr, nullptr);
            if (client == invalid_socket) {
                continue;
            }
            set_receive_timeout(client);
            try {
                serve(client, static_dir, store, worker);
            } catch (const HttpError& error) {
                respond_error(client, error.status, error.what());
            } catch (const PdfError& error) {
                respond_error(client, 422, error.what());
            } catch (const std::exception& error) {
                std::cerr << "Request failed: " << error.what() << '\n';
                respond_error(client, 500, "The request could not be completed.");
            }
            close_socket(client);
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
