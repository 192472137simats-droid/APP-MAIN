#include "paper_store.hpp"

#include <sqlite3.h>

#include <memory>
#include <stdexcept>

namespace {

using Statement = std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)>;

Statement prepare(sqlite3* db, const char* sql) {
    sqlite3_stmt* raw = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &raw, nullptr) != SQLITE_OK) {
        throw std::runtime_error(sqlite3_errmsg(db));
    }
    return Statement(raw, sqlite3_finalize);
}

std::string column_text(sqlite3_stmt* statement, int column) {
    const auto* value = sqlite3_column_text(statement, column);
    return value ? std::string(reinterpret_cast<const char*>(value),
                               static_cast<std::size_t>(sqlite3_column_bytes(statement, column)))
                 : "";
}

PaperRecord read_paper(sqlite3_stmt* statement) {
    PaperRecord paper;
    paper.id = column_text(statement, 0);
    paper.filename = column_text(statement, 1);
    paper.title = column_text(statement, 2);
    paper.pages = sqlite3_column_int(statement, 3);
    paper.characters = static_cast<std::size_t>(sqlite3_column_int64(statement, 4));
    paper.full_text = column_text(statement, 5);
    paper.created_at = column_text(statement, 6);
    paper.analysis_json = column_text(statement, 7);
    paper.analysis_status = column_text(statement, 8);
    paper.analysis_error = column_text(statement, 9);
    return paper;
}

bool has_column(sqlite3* db, const std::string& name) {
    auto statement = prepare(db, "PRAGMA table_info(papers)");
    int result;
    while ((result = sqlite3_step(statement.get())) == SQLITE_ROW) {
        if (column_text(statement.get(), 1) == name) {
            return true;
        }
    }
    if (result != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db));
    }
    return false;
}

void execute(sqlite3* db, const char* sql) {
    if (sqlite3_exec(db, sql, nullptr, nullptr, nullptr) != SQLITE_OK) {
        throw std::runtime_error(sqlite3_errmsg(db));
    }
}

}  // namespace

PaperStore::PaperStore(const std::filesystem::path& database_path) {
    std::filesystem::create_directories(database_path.parent_path());
    const std::string filename = database_path.string();
    if (sqlite3_open(filename.c_str(), &db_) != SQLITE_OK) {
        const std::string error = db_ ? sqlite3_errmsg(db_) : "could not open SQLite database";
        sqlite3_close(db_);
        db_ = nullptr;
        throw std::runtime_error(error);
    }
    sqlite3_busy_timeout(db_, 5000);
    constexpr const char* schema =
        "CREATE TABLE IF NOT EXISTS papers ("
        "id TEXT PRIMARY KEY,"
        "filename TEXT NOT NULL,"
        "title TEXT NOT NULL,"
        "pages INTEGER NOT NULL,"
        "characters INTEGER NOT NULL,"
        "full_text TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')) ,"
        "analysis_json TEXT NOT NULL DEFAULT '',"
        "analysis_status TEXT NOT NULL DEFAULT 'extracted',"
        "analysis_error TEXT NOT NULL DEFAULT ''"
        ");";
    try {
        execute(db_, schema);
        if (!has_column(db_, "analysis_json")) {
            execute(db_, "ALTER TABLE papers ADD COLUMN analysis_json TEXT NOT NULL DEFAULT ''");
        }
        if (!has_column(db_, "analysis_status")) {
            execute(db_, "ALTER TABLE papers ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'extracted'");
        }
        if (!has_column(db_, "analysis_error")) {
            execute(db_, "ALTER TABLE papers ADD COLUMN analysis_error TEXT NOT NULL DEFAULT ''");
        }
    } catch (const std::exception&) {
        const std::string error = sqlite3_errmsg(db_);
        sqlite3_close(db_);
        db_ = nullptr;
        throw std::runtime_error(error);
    }
}

PaperStore::~PaperStore() {
    sqlite3_close(db_);
}

void PaperStore::save(const PaperRecord& paper) {
    auto statement = prepare(db_,
        "INSERT INTO papers (id, filename, title, pages, characters, full_text) "
        "VALUES (?, ?, ?, ?, ?, ?)");
    sqlite3_bind_text(statement.get(), 1, paper.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(statement.get(), 2, paper.filename.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(statement.get(), 3, paper.title.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(statement.get(), 4, paper.pages);
    sqlite3_bind_int64(statement.get(), 5, static_cast<sqlite3_int64>(paper.characters));
    sqlite3_bind_text(statement.get(), 6, paper.full_text.c_str(),
                      static_cast<int>(paper.full_text.size()), SQLITE_TRANSIENT);
    if (sqlite3_step(statement.get()) != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
}

std::optional<PaperRecord> PaperStore::get(const std::string& id) const {
    auto statement = prepare(db_,
        "SELECT id, filename, title, pages, characters, full_text, created_at, "
        "analysis_json, analysis_status, analysis_error "
        "FROM papers WHERE id = ?");
    sqlite3_bind_text(statement.get(), 1, id.c_str(), -1, SQLITE_TRANSIENT);
    const int result = sqlite3_step(statement.get());
    if (result == SQLITE_ROW) {
        return read_paper(statement.get());
    }
    if (result != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
    return std::nullopt;
}

std::vector<PaperRecord> PaperStore::list() const {
    auto statement = prepare(db_,
        "SELECT id, filename, title, pages, characters, '', created_at, '', "
        "analysis_status, analysis_error "
        "FROM papers ORDER BY created_at DESC, rowid DESC");
    std::vector<PaperRecord> papers;
    int result;
    while ((result = sqlite3_step(statement.get())) == SQLITE_ROW) {
        papers.push_back(read_paper(statement.get()));
    }
    if (result != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
    return papers;
}

bool PaperStore::mark_analysis_pending(const std::string& id) {
    auto statement = prepare(db_,
        "UPDATE papers SET analysis_status = 'pending', analysis_json = '', "
        "analysis_error = '' WHERE id = ? AND analysis_status != 'pending'");
    sqlite3_bind_text(statement.get(), 1, id.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(statement.get()) != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
    return sqlite3_changes(db_) == 1;
}

void PaperStore::set_analysis_complete(const std::string& id, const std::string& json) {
    auto statement = prepare(db_,
        "UPDATE papers SET analysis_json = ?, analysis_status = 'done', "
        "analysis_error = '' WHERE id = ? AND analysis_status = 'pending'");
    sqlite3_bind_text(statement.get(), 1, json.c_str(), static_cast<int>(json.size()), SQLITE_TRANSIENT);
    sqlite3_bind_text(statement.get(), 2, id.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(statement.get()) != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
}

void PaperStore::set_analysis_error(const std::string& id, const std::string& error) {
    auto statement = prepare(db_,
        "UPDATE papers SET analysis_status = 'error', analysis_error = ? "
        "WHERE id = ? AND analysis_status = 'pending'");
    sqlite3_bind_text(statement.get(), 1, error.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(statement.get(), 2, id.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(statement.get()) != SQLITE_DONE) {
        throw std::runtime_error(sqlite3_errmsg(db_));
    }
}

void PaperStore::reap_stalled_analyses() {
    execute(db_, "UPDATE papers SET analysis_status = 'error', "
                 "analysis_error = 'Analysis interrupted by server restart.' "
                 "WHERE analysis_status = 'pending'");
}
