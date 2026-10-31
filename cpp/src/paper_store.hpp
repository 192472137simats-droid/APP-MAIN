#pragma once

#include <cstddef>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

struct sqlite3;

struct PaperRecord {
    std::string id;
    std::string filename;
    std::string title;
    int pages = 0;
    std::size_t characters = 0;
    std::string full_text;
    std::string created_at;
    std::string analysis_json;
    std::string analysis_status = "extracted";
    std::string analysis_error;
};

class PaperStore {
public:
    explicit PaperStore(const std::filesystem::path& database_path);
    ~PaperStore();
    PaperStore(const PaperStore&) = delete;
    PaperStore& operator=(const PaperStore&) = delete;

    void save(const PaperRecord& paper);
    std::optional<PaperRecord> get(const std::string& id) const;
    std::vector<PaperRecord> list() const;
    bool mark_analysis_pending(const std::string& id);
    void set_analysis_complete(const std::string& id, const std::string& json);
    void set_analysis_error(const std::string& id, const std::string& error);
    void reap_stalled_analyses();

private:
    sqlite3* db_ = nullptr;
};
