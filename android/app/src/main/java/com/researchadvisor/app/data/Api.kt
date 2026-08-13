package com.researchadvisor.app.data

import com.researchadvisor.app.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class ApiException(message: String) : Exception(message)

object Api {

    var baseUrl: String = BuildConfig.API_BASE_URL.trimEnd('/')

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
        explicitNulls = false
    }

    // Analysis can take ~60s and grounding ~90s, so read timeouts are generous.
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .readTimeout(240, TimeUnit.SECONDS)
        .callTimeout(300, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    private suspend fun execute(request: Request): String = withContext(Dispatchers.IO) {
        try {
            client.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    val detail = runCatching {
                        json.decodeFromString<ErrorBody>(body).detail
                    }.getOrNull()
                    throw ApiException(
                        detail?.takeIf { it.isNotBlank() }
                            ?: "Request failed (HTTP ${response.code})"
                    )
                }
                body
            }
        } catch (e: ApiException) {
            throw e
        } catch (e: Exception) {
            throw ApiException(e.message ?: "Network error - is the server reachable?")
        }
    }

    suspend fun health(): Health =
        json.decodeFromString(execute(Request.Builder().url("$baseUrl/api/health").get().build()))

    suspend fun library(): List<LibraryItem> =
        json.decodeFromString(execute(Request.Builder().url("$baseUrl/api/papers").get().build()))

    suspend fun paper(id: String): Paper =
        json.decodeFromString(execute(Request.Builder().url("$baseUrl/api/papers/$id").get().build()))

    suspend fun upload(filename: String, bytes: ByteArray): UploadResponse {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "file",
                filename,
                bytes.toRequestBody("application/pdf".toMediaType()),
            )
            .build()
        val request = Request.Builder().url("$baseUrl/api/upload").post(body).build()
        return json.decodeFromString(execute(request))
    }

    suspend fun ground(id: String): Grounding {
        val request = Request.Builder()
            .url("$baseUrl/api/papers/$id/ground")
            .post(ByteArray(0).toRequestBody(null, 0, 0))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun chat(paperId: String, question: String): ChatResponse {
        val payload = json.encodeToString(ChatRequest.serializer(), ChatRequest(paperId, question))
        val request = Request.Builder()
            .url("$baseUrl/api/chat")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun delete(id: String) {
        execute(Request.Builder().url("$baseUrl/api/papers/$id").delete().build())
    }

    suspend fun importUrl(url: String): UploadResponse {
        val payload = json.encodeToString(ImportRequest.serializer(), ImportRequest(url))
        val request = Request.Builder()
            .url("$baseUrl/api/import")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun citation(id: String): Citation =
        json.decodeFromString(
            execute(Request.Builder().url("$baseUrl/api/papers/$id/citation").get().build())
        )

    suspend fun topic(paperId: String, gapId: String): Topic {
        val payload = json.encodeToString(TopicRequest.serializer(), TopicRequest(gapId))
        val request = Request.Builder()
            .url("$baseUrl/api/papers/$paperId/topic")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun compare(ids: List<String>): Comparison {
        val payload = json.encodeToString(IdsRequest.serializer(), IdsRequest(ids))
        val request = Request.Builder()
            .url("$baseUrl/api/compare")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun review(ids: List<String>): Review {
        val payload = json.encodeToString(IdsRequest.serializer(), IdsRequest(ids))
        val request = Request.Builder()
            .url("$baseUrl/api/review")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    suspend fun askLibrary(question: String): LibraryAnswer {
        val payload =
            json.encodeToString(LibraryChatRequest.serializer(), LibraryChatRequest(question))
        val request = Request.Builder()
            .url("$baseUrl/api/chat/library")
            .post(payload.toRequestBody(jsonMedia))
            .build()
        return json.decodeFromString(execute(request))
    }

    fun exportUrl(id: String, format: String = "md"): String =
        "$baseUrl/api/papers/$id/export?format=$format"
}
