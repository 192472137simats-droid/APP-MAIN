package com.researchadvisor.app.vm

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.researchadvisor.app.data.Api
import com.researchadvisor.app.data.Citation
import com.researchadvisor.app.data.Comparison
import com.researchadvisor.app.data.LibraryItem
import com.researchadvisor.app.data.Message
import com.researchadvisor.app.data.Paper
import com.researchadvisor.app.data.Review
import com.researchadvisor.app.data.Topic
import com.researchadvisor.app.data.UploadResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Stage(val label: String) {
    IDLE(""),
    READING("Reading the PDF"),
    FETCHING("Fetching the paper"),
    ANALYSING("Analysing paper"),
    SEARCHING("Searching literature"),
}

data class UiState(
    val library: List<LibraryItem> = emptyList(),
    val paper: Paper? = null,
    val warnings: List<String> = emptyList(),
    val stage: Stage = Stage.IDLE,
    val loadingLibrary: Boolean = false,
    val grounding: Boolean = false,
    val asking: Boolean = false,
    val error: String? = null,
    val toast: String? = null,
    val serverOk: Boolean? = null,
    val keyConfigured: Boolean = true,

    // Feature surfaces
    val citation: Citation? = null,
    val topic: Topic? = null,
    val topicFor: String? = null,
    val topicLoading: String? = null,
    val comparison: Comparison? = null,
    val review: Review? = null,
    val working: String? = null,
    val selected: Set<String> = emptySet(),
    val libraryMessages: List<Message> = emptyList(),
    val askingLibrary: Boolean = false,
)

class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        checkHealth()
        refreshLibrary()
    }

    fun dismissError() = _state.update { it.copy(error = null) }
    fun dismissToast() = _state.update { it.copy(toast = null) }
    fun dismissCitation() = _state.update { it.copy(citation = null) }
    fun dismissTopic() = _state.update { it.copy(topic = null, topicFor = null) }
    fun dismissComparison() = _state.update { it.copy(comparison = null) }
    fun dismissReview() = _state.update { it.copy(review = null) }
    fun closePaper() = _state.update { it.copy(paper = null, warnings = emptyList()) }

    fun toggleSelected(id: String) = _state.update { current ->
        val next = current.selected.toMutableSet()
        if (!next.remove(id) && next.size < 5) next.add(id)
        current.copy(selected = next)
    }

    private fun fail(exception: Throwable) =
        _state.update { it.copy(error = exception.message, working = null, stage = Stage.IDLE) }

    private fun checkHealth() = viewModelScope.launch {
        runCatching { Api.health() }
            .onSuccess { health ->
                _state.update {
                    it.copy(serverOk = true, keyConfigured = health.gemini_key_configured)
                }
            }
            .onFailure { _state.update { it.copy(serverOk = false) } }
    }

    fun refreshLibrary() = viewModelScope.launch {
        _state.update { it.copy(loadingLibrary = true) }
        runCatching { Api.library() }
            .onSuccess { items -> _state.update { it.copy(library = items, loadingLibrary = false) } }
            .onFailure { e -> _state.update { it.copy(loadingLibrary = false, error = e.message) } }
    }

    fun openPaper(id: String) = viewModelScope.launch {
        runCatching { Api.paper(id) }
            .onSuccess { paper -> _state.update { it.copy(paper = paper, warnings = emptyList()) } }
            .onFailure(::fail)
    }

    fun deletePaper(id: String) = viewModelScope.launch {
        runCatching { Api.delete(id) }
            .onSuccess {
                _state.update { it.copy(paper = null, toast = "Paper deleted") }
                refreshLibrary()
            }
            .onFailure(::fail)
    }

    // --- ingestion --------------------------------------------------------

    fun upload(uri: Uri) = viewModelScope.launch {
        _state.update { it.copy(stage = Stage.READING, error = null) }
        val resolver = getApplication<Application>().contentResolver

        val filename = withContext(Dispatchers.IO) {
            runCatching {
                resolver.query(uri, null, null, null, null)?.use { cursor ->
                    val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
                }
            }.getOrNull() ?: "paper.pdf"
        }

        val bytes = withContext(Dispatchers.IO) {
            runCatching { resolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
        }

        if (bytes == null || bytes.isEmpty()) {
            _state.update { it.copy(stage = Stage.IDLE, error = "Could not read that file.") }
            return@launch
        }
        if (bytes.size > 25 * 1024 * 1024) {
            _state.update {
                it.copy(stage = Stage.IDLE, error = "That PDF is larger than the 25 MB limit.")
            }
            return@launch
        }

        _state.update { it.copy(stage = Stage.ANALYSING) }
        runCatching { Api.upload(filename, bytes) }
            .onSuccess { afterIngest(it) }
            .onFailure(::fail)
    }

    /** arXiv id, arXiv link or DOI. */
    fun importUrl(url: String) = viewModelScope.launch {
        if (url.isBlank()) return@launch
        _state.update { it.copy(stage = Stage.FETCHING, error = null) }
        runCatching { Api.importUrl(url.trim()) }
            .onSuccess { afterIngest(it) }
            .onFailure(::fail)
    }

    private suspend fun afterIngest(uploaded: UploadResponse) {
        runCatching { Api.paper(uploaded.paper_id) }.onSuccess { paper ->
            _state.update { it.copy(paper = paper, warnings = uploaded.warnings) }
        }
        refreshLibrary()

        _state.update { it.copy(stage = Stage.SEARCHING, grounding = true) }
        runCatching { Api.ground(uploaded.paper_id) }
            .onSuccess { grounding ->
                _state.update { current ->
                    current.copy(
                        stage = Stage.IDLE,
                        grounding = false,
                        paper = current.paper?.copy(grounding = grounding),
                        toast = "Checked ${grounding.corpus_size} papers from the literature",
                    )
                }
                refreshLibrary()
            }
            .onFailure { e ->
                _state.update {
                    it.copy(
                        stage = Stage.IDLE,
                        grounding = false,
                        error = "Gap grounding failed: ${e.message}",
                    )
                }
            }
    }

    fun ground() = viewModelScope.launch {
        val id = _state.value.paper?.id ?: return@launch
        _state.update { it.copy(grounding = true, error = null) }
        runCatching { Api.ground(id) }
            .onSuccess { grounding ->
                _state.update { current ->
                    current.copy(
                        grounding = false,
                        paper = current.paper?.copy(grounding = grounding),
                        toast = "Checked ${grounding.corpus_size} papers",
                    )
                }
                refreshLibrary()
            }
            .onFailure { e -> _state.update { it.copy(grounding = false, error = e.message) } }
    }

    // --- chat -------------------------------------------------------------

    fun ask(question: String) = viewModelScope.launch {
        val paper = _state.value.paper ?: return@launch
        if (question.isBlank()) return@launch

        _state.update { current ->
            current.copy(
                asking = true,
                paper = current.paper?.copy(
                    messages = current.paper.messages + Message("user", question),
                ),
            )
        }

        runCatching { Api.chat(paper.id, question) }
            .onSuccess { response ->
                _state.update { current ->
                    current.copy(
                        asking = false,
                        paper = current.paper?.copy(
                            messages = current.paper.messages +
                                Message("assistant", response.answer),
                        ),
                    )
                }
            }
            .onFailure { e ->
                _state.update { current ->
                    current.copy(
                        asking = false,
                        paper = current.paper?.copy(
                            messages = current.paper.messages +
                                Message("assistant", "Something went wrong: ${e.message}"),
                        ),
                    )
                }
            }
    }

    fun askLibrary(question: String) = viewModelScope.launch {
        if (question.isBlank()) return@launch
        _state.update {
            it.copy(
                askingLibrary = true,
                libraryMessages = it.libraryMessages + Message("user", question),
            )
        }
        runCatching { Api.askLibrary(question) }
            .onSuccess { answer ->
                _state.update {
                    it.copy(
                        askingLibrary = false,
                        libraryMessages = it.libraryMessages +
                            Message("assistant", answer.answer),
                    )
                }
            }
            .onFailure { e ->
                _state.update {
                    it.copy(
                        askingLibrary = false,
                        libraryMessages = it.libraryMessages +
                            Message("assistant", "Something went wrong: ${e.message}"),
                    )
                }
            }
    }

    // --- feature surfaces -------------------------------------------------

    fun loadCitation() = viewModelScope.launch {
        val id = _state.value.paper?.id ?: return@launch
        _state.update { it.copy(working = "Building citation") }
        runCatching { Api.citation(id) }
            .onSuccess { c -> _state.update { it.copy(citation = c, working = null) } }
            .onFailure(::fail)
    }

    fun generateTopic(gapId: String) = viewModelScope.launch {
        val id = _state.value.paper?.id ?: return@launch
        _state.update { it.copy(topicLoading = gapId, error = null) }
        runCatching { Api.topic(id, gapId) }
            .onSuccess { t ->
                _state.update { it.copy(topic = t, topicFor = gapId, topicLoading = null) }
            }
            .onFailure { e -> _state.update { it.copy(topicLoading = null, error = e.message) } }
    }

    fun runCompare() = viewModelScope.launch {
        val ids = _state.value.selected.toList()
        if (ids.size < 2) {
            _state.update { it.copy(error = "Select at least 2 papers.") }
            return@launch
        }
        _state.update { it.copy(working = "Comparing ${ids.size} papers", error = null) }
        runCatching { Api.compare(ids) }
            .onSuccess { c -> _state.update { it.copy(comparison = c, working = null) } }
            .onFailure(::fail)
    }

    fun runReview() = viewModelScope.launch {
        val ids = _state.value.selected.toList()
        if (ids.size < 2) {
            _state.update { it.copy(error = "Select at least 2 papers.") }
            return@launch
        }
        _state.update { it.copy(working = "Writing literature review", error = null) }
        runCatching { Api.review(ids) }
            .onSuccess { r -> _state.update { it.copy(review = r, working = null) } }
            .onFailure(::fail)
    }
}
