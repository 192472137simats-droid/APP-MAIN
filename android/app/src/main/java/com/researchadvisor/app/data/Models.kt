package com.researchadvisor.app.data

import kotlinx.serialization.Serializable

/**
 * Every field carries a default and optional fields are nullable.
 *
 * This is deliberate: the backend's shape changes between stages. `grounding`
 * is null until literature validation runs, and gaps only grow `verdict`,
 * `confidence`, `reasoning`, `what_remains` and `evidence` afterwards. Gemini's
 * response schema also marks several fields optional, so they can be absent
 * entirely. Strict deserialization would crash on the very first upload.
 */

@Serializable
data class Metadata(
    val title: String = "",
    val authors: List<String> = emptyList(),
    val year: Int? = null,
    val venue: String = "",
    val domain: String = "",
    val keywords: List<String> = emptyList(),
)

@Serializable
data class Summary(
    val one_line: String = "",
    val short: String = "",
    val detailed: String = "",
    val eli5: String = "",
)

/** A retrieved OpenAlex work, used both as corpus entry and as gap evidence. */
@Serializable
data class Evidence(
    val openalex_id: String = "",
    val title: String = "",
    val year: Int? = null,
    val doi: String = "",
    val url: String = "",
    val venue: String = "",
    val citations: Int = 0,
    val authors: List<String> = emptyList(),
    val abstract: String = "",
    val source: String = "",
    val ref: Int = 0,
)

@Serializable
data class Gap(
    val id: String = "",
    val title: String = "",
    val category: String = "",
    val description: String = "",
    val evidence_from_paper: String = "",
    val origin: String = "",
    val search_query: String = "",
    // Present only after grounding.
    val verdict: String? = null,
    val confidence: String? = null,
    val reasoning: String = "",
    val what_remains: String = "",
    val evidence: List<Evidence> = emptyList(),
)

@Serializable
data class Analysis(
    val metadata: Metadata = Metadata(),
    val summary: Summary = Summary(),
    val objective: String = "",
    val method: String = "",
    val datasets: List<String> = emptyList(),
    val metrics: List<String> = emptyList(),
    val contributions: List<String> = emptyList(),
    val findings: List<String> = emptyList(),
    val conclusion: String = "",
    val stated_limitations: List<String> = emptyList(),
    val stated_future_work: List<String> = emptyList(),
    val gaps: List<Gap> = emptyList(),
)

@Serializable
data class Grounding(
    val matched_paper: Boolean = false,
    val matched_title: String = "",
    val paper_year: Int? = null,
    val corpus: List<Evidence> = emptyList(),
    val corpus_size: Int = 0,
    val queries: List<String> = emptyList(),
    val gaps: List<Gap> = emptyList(),
)

@Serializable
data class Message(
    val role: String = "",
    val content: String = "",
)

@Serializable
data class Paper(
    val id: String = "",
    val filename: String = "",
    val created_at: String = "",
    val pages: Int = 0,
    val characters: Int = 0,
    val title: String = "",
    val analysis: Analysis? = null,
    val grounding: Grounding? = null,
    val messages: List<Message> = emptyList(),
) {
    /** Grounded gaps when available, otherwise the ungrounded ones. */
    val gaps: List<Gap>
        get() = grounding?.gaps?.takeIf { it.isNotEmpty() } ?: analysis?.gaps ?: emptyList()

    val displayTitle: String
        get() = analysis?.metadata?.title?.takeIf { it.isNotBlank() }
            ?: title.takeIf { it.isNotBlank() }
            ?: filename
}

@Serializable
data class LibraryItem(
    val id: String = "",
    val filename: String = "",
    val created_at: String = "",
    val pages: Int = 0,
    val title: String = "",
    val year: Int? = null,
    val authors: List<String> = emptyList(),
    val domain: String? = null,
    val grounded: Boolean = false,
    val open_gaps: Int = 0,
    val total_gaps: Int = 0,
)

@Serializable
data class UploadResponse(
    val paper_id: String = "",
    val filename: String = "",
    val pages: Int = 0,
    val characters: Int = 0,
    val sections_detected: List<String> = emptyList(),
    val warnings: List<String> = emptyList(),
    val analysis: Analysis = Analysis(),
)

@Serializable
data class ChatRequest(
    val paper_id: String,
    val question: String,
)

@Serializable
data class ChatResponse(
    val answer: String = "",
)

@Serializable
data class Health(
    val status: String = "",
    val model: String = "",
    val gemini_key_configured: Boolean = false,
    val papers: Int = 0,
)

/** FastAPI returns {"detail": "..."} on error. */
@Serializable
data class ErrorBody(
    val detail: String = "",
)

// --- added feature DTOs ---------------------------------------------------

@Serializable
data class Citation(
    val ieee: String = "",
    val apa: String = "",
    val mla: String = "",
    val chicago: String = "",
    val bibtex: String = "",
)

@Serializable
data class Topic(
    val title: String = "",
    val problem: String = "",
    val why_it_is_open: String = "",
    val proposed_approach: String = "",
    val datasets: List<String> = emptyList(),
    val evaluation_plan: String = "",
    val expected_contribution: String = "",
    val prerequisites: List<String> = emptyList(),
    val difficulty: String = "",
    val risks: List<String> = emptyList(),
)

@Serializable
data class CompareRow(
    val aspect: String = "",
    val values: List<String> = emptyList(),
)

@Serializable
data class ComparePaper(
    val id: String = "",
    val title: String = "",
    val year: Int? = null,
)

@Serializable
data class Comparison(
    val rows: List<CompareRow> = emptyList(),
    val shared_gaps: List<String> = emptyList(),
    val contradictions: List<String> = emptyList(),
    val synthesis: String = "",
    val papers: List<ComparePaper> = emptyList(),
)

@Serializable
data class ReviewTheme(
    val name: String = "",
    val discussion: String = "",
    val papers: List<Int> = emptyList(),
)

@Serializable
data class ReviewReference(
    val number: Int = 0,
    val id: String = "",
    val citation: String = "",
)

@Serializable
data class Review(
    val title: String = "",
    val introduction: String = "",
    val themes: List<ReviewTheme> = emptyList(),
    val common_limitations: List<String> = emptyList(),
    val unresolved_problems: List<String> = emptyList(),
    val research_opportunity: String = "",
    val conclusion: String = "",
    val references: List<ReviewReference> = emptyList(),
)

@Serializable
data class LibraryAnswer(
    val answer: String = "",
    val papers_searched: Int = 0,
)

@Serializable
data class ImportRequest(val url: String)

@Serializable
data class LibraryChatRequest(val question: String)

@Serializable
data class TopicRequest(val gap_id: String)

@Serializable
data class IdsRequest(val paper_ids: List<String>)
