#pragma once

#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <queue>
#include <string>
#include <thread>

struct LlmConfig {
    std::string base_url;
    std::string api_key;
    std::string model;

    static LlmConfig load(const std::filesystem::path& env_file);
    bool configured() const { return !api_key.empty() && !model.empty(); }
};

class AnalysisWorker {
public:
    AnalysisWorker(std::filesystem::path database_path, LlmConfig config);
    ~AnalysisWorker();
    AnalysisWorker(const AnalysisWorker&) = delete;
    AnalysisWorker& operator=(const AnalysisWorker&) = delete;

    bool configured() const { return config_.configured(); }
    const std::string& model() const { return config_.model; }
    std::string analyze_text(const std::string& paper_text) const;
    void enqueue(std::string paper_id);

private:
    void run();

    std::filesystem::path database_path_;
    LlmConfig config_;
    std::thread thread_;
    std::mutex mutex_;
    std::condition_variable condition_;
    std::queue<std::string> pending_;
    bool stopping_ = false;
};
