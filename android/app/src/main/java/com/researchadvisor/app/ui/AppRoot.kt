package com.researchadvisor.app.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.FormatQuote
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.researchadvisor.app.data.Api
import com.researchadvisor.app.data.Evidence
import com.researchadvisor.app.data.Gap
import com.researchadvisor.app.data.LibraryItem
import com.researchadvisor.app.data.Message
import com.researchadvisor.app.data.Paper
import com.researchadvisor.app.vm.AppViewModel
import com.researchadvisor.app.vm.Stage

@Composable
fun AppRoot(vm: AppViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); vm.dismissError() }
    }
    LaunchedEffect(state.toast) {
        state.toast?.let { snackbar.showSnackbar(it); vm.dismissToast() }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Box(Modifier.padding(padding)) {
            val paper = state.paper
            if (paper == null) {
                HomeScreen(vm, state)
            } else {
                PaperScreen(
                    paper = paper,
                    warnings = state.warnings,
                    grounding = state.grounding,
                    asking = state.asking,
                    topicLoading = state.topicLoading,
                    onBack = vm::closePaper,
                    onGround = vm::ground,
                    onAsk = vm::ask,
                    onDelete = { vm.deletePaper(paper.id) },
                    onCitation = vm::loadCitation,
                    onTopic = vm::generateTopic,
                )
            }

            // Dialogs sit above whichever screen is showing.
            state.citation?.let { CitationDialog(it, vm::dismissCitation) }
            state.topic?.let { TopicDialog(it, vm::dismissTopic) }
            state.comparison?.let { ComparisonDialog(it, vm::dismissComparison) }
            state.review?.let { ReviewDialog(it, vm::dismissReview) }
            state.working?.let { WorkingDialog(it) }
        }
    }
}

/* ================================================================== */
/* Home: Papers | Ask | Compare                                        */
/* ================================================================== */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeScreen(vm: AppViewModel, state: com.researchadvisor.app.vm.UiState) {
    var tab by remember { mutableStateOf(0) }
    var showImport by remember { mutableStateOf(false) }
    val busy = state.stage != Stage.IDLE

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::upload) }

    if (showImport) {
        ImportDialog(
            onDismiss = { showImport = false },
            onImport = { url -> showImport = false; vm.importUrl(url) },
        )
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("ResearchAdvisorLM", fontWeight = FontWeight.SemiBold)
                        Text(
                            "Find what the paper didn't do",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = vm::refreshLibrary) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        floatingActionButton = {
            if (tab == 0) {
                ExtendedFloatingActionButton(
                    onClick = { if (!busy) picker.launch(arrayOf("application/pdf")) },
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = Color.White,
                ) {
                    Icon(Icons.Filled.Add, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text(if (busy) "Working…" else "Upload PDF")
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            TabRow(
                selectedTabIndex = tab,
                containerColor = MaterialTheme.colorScheme.background,
            ) {
                listOf("Papers", "Ask library", "Compare").forEachIndexed { index, label ->
                    Tab(
                        selected = tab == index,
                        onClick = { tab = index },
                        text = { Text(label, style = MaterialTheme.typography.bodyMedium) },
                    )
                }
            }

            Column(Modifier.padding(horizontal = 16.dp)) {
                if (state.serverOk == false) {
                    Banner("Can't reach the server at ${Api.baseUrl}", MaterialTheme.colorScheme.error)
                } else if (!state.keyConfigured) {
                    Banner("Server is up but GEMINI_API_KEY is not configured.", PartialAmber)
                }

                AnimatedVisibility(visible = busy) {
                    Column(Modifier.padding(vertical = 12.dp)) {
                        Text(
                            state.stage.label,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(8.dp))
                        LinearProgressIndicator(
                            modifier = Modifier.fillMaxWidth(),
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "Analysis takes up to a minute, literature search a little longer.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            when (tab) {
                0 -> PapersTab(
                    library = state.library,
                    loading = state.loadingLibrary,
                    onOpen = vm::openPaper,
                    onImportClick = { showImport = true },
                )
                1 -> LibraryChatTab(
                    messages = state.libraryMessages,
                    asking = state.askingLibrary,
                    paperCount = state.library.size,
                    onAsk = vm::askLibrary,
                )
                else -> CompareTab(
                    library = state.library,
                    selected = state.selected,
                    onToggle = vm::toggleSelected,
                    onCompare = vm::runCompare,
                    onReview = vm::runReview,
                )
            }
        }
    }
}

@Composable
private fun PapersTab(
    library: List<LibraryItem>,
    loading: Boolean,
    onOpen: (String) -> Unit,
    onImportClick: () -> Unit,
) {
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        OutlinedButton(
            onClick = onImportClick,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        ) { Text("Import from arXiv link or DOI") }

        when {
            loading && library.isEmpty() -> CenteredSpinner()
            library.isEmpty() -> EmptyState()
            else -> LazyColumn(
                verticalArrangement = Arrangement.spacedBy(10.dp),
                contentPadding = PaddingValues(top = 12.dp, bottom = 96.dp),
            ) {
                items(library, key = { it.id }) { item ->
                    LibraryCard(item, selected = false) { onOpen(item.id) }
                }
            }
        }
    }
}

@Composable
private fun CompareTab(
    library: List<LibraryItem>,
    selected: Set<String>,
    onToggle: (String) -> Unit,
    onCompare: () -> Unit,
    onReview: () -> Unit,
) {
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Text(
            "Select 2–5 papers. The most useful output is what none of them address.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onCompare, enabled = selected.size >= 2) { Text("Compare") }
            OutlinedButton(onClick = onReview, enabled = selected.size >= 2) {
                Text("Literature review")
            }
        }
        if (library.size < 2) {
            EmptyState("Upload at least two papers to compare them.")
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(10.dp),
                contentPadding = PaddingValues(top = 12.dp, bottom = 40.dp),
            ) {
                items(library, key = { it.id }) { item ->
                    LibraryCard(item, selected = item.id in selected) { onToggle(item.id) }
                }
            }
        }
    }
}

@Composable
private fun LibraryChatTab(
    messages: List<Message>,
    asking: Boolean,
    paperCount: Int,
    onAsk: (String) -> Unit,
) {
    var text by remember { mutableStateOf("") }
    val listState = rememberLazyListState()

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Column(Modifier.fillMaxSize()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(vertical = 14.dp),
        ) {
            if (messages.isEmpty()) {
                item {
                    Bubble(
                        "Ask across all $paperCount paper(s) in your library — what they have " +
                            "in common, which ones use a given dataset, what nobody has tried.",
                        fromUser = false,
                    )
                }
            }
            items(messages) { message ->
                Bubble(message.content, fromUser = message.role == "user")
            }
            if (asking) item { Bubble("Searching the library…", fromUser = false) }
        }
        ChatInput(
            value = text,
            onValue = { text = it },
            enabled = !asking,
            placeholder = "Ask across your library…",
            onSend = { if (text.isNotBlank()) { onAsk(text); text = "" } },
        )
    }
}

@Composable
private fun LibraryCard(item: LibraryItem, selected: Boolean, onClick: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .border(
                if (selected) 2.dp else 1.dp,
                if (selected) Accent else MaterialTheme.colorScheme.outline,
                RoundedCornerShape(14.dp),
            )
            .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .padding(16.dp),
    ) {
        Text(
            item.title.ifBlank { item.filename },
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            item.year?.let { MetaText("$it") }
            MetaText("${item.pages}p")
            if (item.grounded) {
                Text(
                    "${item.open_gaps} open / ${item.total_gaps} gaps",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (item.open_gaps > 0) OpenRed else AddressedGreen,
                )
            } else {
                MetaText("${item.total_gaps} gaps · not grounded")
            }
        }
    }
}

/* ================================================================== */
/* Paper                                                               */
/* ================================================================== */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PaperScreen(
    paper: Paper,
    warnings: List<String>,
    grounding: Boolean,
    asking: Boolean,
    topicLoading: String?,
    onBack: () -> Unit,
    onGround: () -> Unit,
    onAsk: (String) -> Unit,
    onDelete: () -> Unit,
    onCitation: () -> Unit,
    onTopic: (String) -> Unit,
) {
    var tab by remember { mutableStateOf(0) }
    var exportOpen by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val tabs = listOf("Summary", "Gaps (${paper.gaps.size})", "Chat")

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        paper.displayTitle,
                        maxLines = 2,
                        style = MaterialTheme.typography.titleSmall,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = onCitation) {
                        Icon(Icons.Filled.FormatQuote, contentDescription = "Citation")
                    }
                    Box {
                        IconButton(onClick = { exportOpen = true }) {
                            Icon(Icons.Filled.Download, contentDescription = "Export")
                        }
                        DropdownMenu(
                            expanded = exportOpen,
                            onDismissRequest = { exportOpen = false },
                        ) {
                            listOf(
                                "PDF report" to "pdf",
                                "Word (.docx)" to "docx",
                                "Markdown" to "md",
                            ).forEach { (label, format) ->
                                DropdownMenuItem(
                                    text = { Text(label) },
                                    onClick = {
                                        exportOpen = false
                                        context.startActivity(
                                            Intent(
                                                Intent.ACTION_VIEW,
                                                Uri.parse(Api.exportUrl(paper.id, format)),
                                            ),
                                        )
                                    },
                                )
                            }
                        }
                    }
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Filled.Delete, contentDescription = "Delete")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            TabRow(
                selectedTabIndex = tab,
                containerColor = MaterialTheme.colorScheme.background,
            ) {
                tabs.forEachIndexed { index, label ->
                    Tab(
                        selected = tab == index,
                        onClick = { tab = index },
                        text = { Text(label, style = MaterialTheme.typography.bodyMedium) },
                    )
                }
            }

            when (tab) {
                0 -> SummaryTab(paper, warnings)
                1 -> GapsTab(paper, grounding, topicLoading, onGround, onTopic)
                else -> ChatTab(paper, asking, onAsk)
            }
        }
    }
}

@Composable
private fun SummaryTab(paper: Paper, warnings: List<String>) {
    val analysis = paper.analysis
    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(vertical = 14.dp),
    ) {
        if (warnings.isNotEmpty()) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    warnings.forEach { Banner(it, PartialAmber) }
                }
            }
        }
        item {
            val meta = analysis?.metadata
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                meta?.year?.let { MetaText("$it") }
                MetaText("${paper.pages} pages")
                meta?.domain?.takeIf { it.isNotBlank() }?.let { MetaText(it) }
            }
            val authors = meta?.authors.orEmpty()
            if (authors.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                MetaText(authors.take(4).joinToString(", "))
            }
        }
        item { Block("Summary", analysis?.summary?.detailed.orEmpty()) }
        item { Block("Objective", analysis?.objective.orEmpty()) }
        item { Block("Method", analysis?.method.orEmpty()) }
        item { BulletBlock("Datasets", analysis?.datasets.orEmpty()) }
        item { BulletBlock("Contributions", analysis?.contributions.orEmpty()) }
        item { BulletBlock("Key findings", analysis?.findings.orEmpty()) }
        item { BulletBlock("Stated limitations", analysis?.stated_limitations.orEmpty()) }
        item { Block("Conclusion", analysis?.conclusion.orEmpty()) }
        item { Block("In plain language", analysis?.summary?.eli5.orEmpty()) }
    }
}

@Composable
private fun GapsTab(
    paper: Paper,
    grounding: Boolean,
    topicLoading: String?,
    onGround: () -> Unit,
    onTopic: (String) -> Unit,
) {
    val gaps = paper.gaps
    val g = paper.grounding

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(vertical = 14.dp),
    ) {
        item {
            when {
                grounding -> Banner("Searching the literature…", Accent)
                g != null -> {
                    val open = gaps.count { it.verdict == "open" }
                    Column {
                        Banner(
                            "$open of ${gaps.size} directions still look open. Checked against " +
                                "${g.corpus_size} papers from OpenAlex" +
                                (g.paper_year?.let { ", published from $it onward" } ?: "") + ".",
                            Accent,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "A bounded corpus, not the whole literature — treat verdicts as " +
                                "evidence, not proof.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                else -> Column {
                    Banner(
                        "These gaps come from the paper alone and haven't been checked " +
                            "against later work.",
                        PartialAmber,
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = onGround) { Text("Check literature now") }
                }
            }
        }
        itemsIndexed(gaps) { index, gap ->
            GapCard(index + 1, gap, topicLoading == gap.id) { onTopic(gap.id) }
        }
    }
}

@Composable
private fun GapCard(number: Int, gap: Gap, topicBusy: Boolean, onTopic: () -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val color = verdictColor(gap.verdict)

    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(14.dp))
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .width(3.dp)
                    .height(20.dp)
                    .background(color, RoundedCornerShape(2.dp)),
            )
            Spacer(Modifier.width(10.dp))
            Text(
                "Gap $number — ${gap.title}",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
        }

        Spacer(Modifier.height(8.dp))
        Text(verdictLabel(gap.verdict), color = color, fontSize = 11.sp, fontWeight = FontWeight.Bold)

        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            MetaText(gap.category.ifBlank { "general" })
            MetaText(if (gap.origin == "stated") "authors stated it" else "inferred")
            gap.confidence?.let { MetaText("confidence: $it") }
        }

        Spacer(Modifier.height(10.dp))
        Text(gap.description, style = MaterialTheme.typography.bodyMedium)

        if (gap.evidence_from_paper.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(
                "In the paper: ${gap.evidence_from_paper}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (gap.reasoning.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text("Literature check", style = MaterialTheme.typography.labelMedium, color = color)
            Text(gap.reasoning, style = MaterialTheme.typography.bodySmall)
        }

        if (gap.evidence.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            TextButton(onClick = { expanded = !expanded }) {
                Text(
                    if (expanded) "Hide evidence"
                    else "${gap.evidence.size} paper(s) supporting this verdict",
                )
            }
            if (expanded) gap.evidence.forEach { EvidenceRow(it) }
        }

        if (gap.what_remains.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(Accent.copy(alpha = 0.12f), RoundedCornerShape(10.dp))
                    .padding(12.dp),
            ) {
                Text(
                    "What a new project could do",
                    style = MaterialTheme.typography.labelMedium,
                    color = Accent,
                )
                Spacer(Modifier.height(4.dp))
                Text(gap.what_remains, style = MaterialTheme.typography.bodySmall)
            }
        }

        Spacer(Modifier.height(6.dp))
        TextButton(onClick = onTopic, enabled = !topicBusy) {
            Text(if (topicBusy) "Generating…" else "Turn this into a project topic")
        }
    }
}

@Composable
private fun EvidenceRow(work: Evidence) {
    val context = LocalContext.current
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = work.url.isNotBlank()) {
                runCatching {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(work.url)))
                }
            }
            .padding(vertical = 8.dp),
    ) {
        Text(
            work.title,
            style = MaterialTheme.typography.bodySmall,
            color = if (work.url.isNotBlank()) Accent else MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(2.dp))
        MetaText(
            listOfNotNull(
                work.authors.take(2).joinToString(", ").takeIf { it.isNotBlank() },
                work.year?.toString(),
                "${work.citations} citations",
                work.venue.takeIf { it.isNotBlank() },
            ).joinToString(" · "),
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.outline)
    }
}

@Composable
private fun ChatTab(paper: Paper, asking: Boolean, onAsk: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    val messages = paper.messages

    LaunchedEffect(messages.size, asking) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Column(Modifier.fillMaxSize()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(vertical = 14.dp),
        ) {
            if (messages.isEmpty()) {
                item {
                    Bubble(
                        "Ask me anything about this paper. I answer from the paper text and " +
                            "name the section I used.",
                        fromUser = false,
                    )
                }
                item {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf(
                            "Explain this paper in simple terms",
                            "What dataset was used?",
                            "What did they NOT test?",
                        ).forEach { suggestion ->
                            OutlinedButton(onClick = { onAsk(suggestion) }) { Text(suggestion) }
                        }
                    }
                }
            }
            items(messages) { message ->
                Bubble(message.content, fromUser = message.role == "user")
            }
            if (asking) item { Bubble("Reading the paper…", fromUser = false) }
        }
        ChatInput(
            value = text,
            onValue = { text = it },
            enabled = !asking,
            placeholder = "Ask about this paper…",
            onSend = { if (text.isNotBlank()) { onAsk(text); text = "" } },
        )
    }
}

/* ================================================================== */
/* Dialogs                                                             */
/* ================================================================== */

@Composable
private fun ImportDialog(onDismiss: () -> Unit, onImport: (String) -> Unit) {
    var url by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Import a paper") },
        text = {
            Column {
                Text(
                    "Paste an arXiv ID (1706.03762), an arXiv link, a DOI, or a direct " +
                        "PDF URL.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                TextField(
                    value = url,
                    onValueChange = { url = it },
                    singleLine = true,
                    placeholder = { Text("1706.03762") },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(onClick = { onImport(url) }, enabled = url.isNotBlank()) { Text("Import") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun CitationDialog(citation: com.researchadvisor.app.data.Citation, onDismiss: () -> Unit) {
    ScrollDialog("Citation", onDismiss) {
        listOf(
            "IEEE" to citation.ieee,
            "APA" to citation.apa,
            "MLA" to citation.mla,
            "Chicago" to citation.chicago,
        ).forEach { (style, value) ->
            Text(style, style = MaterialTheme.typography.labelMedium, color = Accent)
            Text(value, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(12.dp))
        }
        Text("BibTeX", style = MaterialTheme.typography.labelMedium, color = Accent)
        Text(
            citation.bibtex,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun TopicDialog(topic: com.researchadvisor.app.data.Topic, onDismiss: () -> Unit) {
    ScrollDialog("Project topic", onDismiss) {
        Text(topic.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(4.dp))
        Text(
            topic.difficulty.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = Accent,
        )
        Spacer(Modifier.height(12.dp))
        DialogSection("Problem", topic.problem)
        DialogSection("Why it is still open", topic.why_it_is_open)
        DialogSection("Proposed approach", topic.proposed_approach)
        if (topic.datasets.isNotEmpty()) {
            DialogSection("Datasets", topic.datasets.joinToString(", "))
        }
        DialogSection("Evaluation plan", topic.evaluation_plan)
        DialogSection("Expected contribution", topic.expected_contribution)
        if (topic.prerequisites.isNotEmpty()) {
            DialogSection("Prerequisites", topic.prerequisites.joinToString("\n") { "• $it" })
        }
        if (topic.risks.isNotEmpty()) {
            DialogSection("Risks", topic.risks.joinToString("\n") { "• $it" })
        }
    }
}

@Composable
private fun ComparisonDialog(
    comparison: com.researchadvisor.app.data.Comparison,
    onDismiss: () -> Unit,
) {
    ScrollDialog("Comparison", onDismiss) {
        comparison.rows.forEach { row ->
            Text(row.aspect, style = MaterialTheme.typography.labelMedium, color = Accent)
            row.values.forEachIndexed { index, value ->
                val name = comparison.papers.getOrNull(index)?.title ?: "Paper ${index + 1}"
                Text(
                    "${name.take(38)}: $value",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Spacer(Modifier.height(12.dp))
        }
        if (comparison.shared_gaps.isNotEmpty()) {
            DialogSection(
                "Gaps none of them address",
                comparison.shared_gaps.joinToString("\n") { "• $it" },
            )
        }
        if (comparison.contradictions.isNotEmpty()) {
            DialogSection(
                "Contradictions",
                comparison.contradictions.joinToString("\n") { "• $it" },
            )
        }
        DialogSection("Synthesis", comparison.synthesis)
    }
}

@Composable
private fun ReviewDialog(review: com.researchadvisor.app.data.Review, onDismiss: () -> Unit) {
    ScrollDialog("Literature review", onDismiss) {
        Text(review.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(10.dp))
        DialogSection("Introduction", review.introduction)
        review.themes.forEach { theme ->
            DialogSection(
                theme.name,
                theme.discussion + "\n\nDraws on: " +
                    theme.papers.joinToString(", ") { "[$it]" },
            )
        }
        if (review.common_limitations.isNotEmpty()) {
            DialogSection(
                "Common limitations",
                review.common_limitations.joinToString("\n") { "• $it" },
            )
        }
        if (review.unresolved_problems.isNotEmpty()) {
            DialogSection(
                "Unresolved problems",
                review.unresolved_problems.joinToString("\n") { "• $it" },
            )
        }
        DialogSection("Research opportunity", review.research_opportunity)
        DialogSection("Conclusion", review.conclusion)
        if (review.references.isNotEmpty()) {
            DialogSection(
                "References",
                review.references.joinToString("\n") { "[${it.number}] ${it.citation}" },
            )
        }
    }
}

@Composable
private fun WorkingDialog(label: String) {
    AlertDialog(
        onDismissRequest = {},
        confirmButton = {},
        title = { Text(label) },
        text = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(
                    modifier = Modifier.width(22.dp).height(22.dp),
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.width(14.dp))
                Text("This can take up to a minute.", style = MaterialTheme.typography.bodySmall)
            }
        },
    )
}

@Composable
private fun ScrollDialog(
    title: String,
    onDismiss: () -> Unit,
    content: @Composable () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(
                Modifier
                    .heightIn(max = 460.dp)
                    .verticalScroll(rememberScrollState()),
            ) { content() }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun DialogSection(title: String, body: String) {
    if (body.isBlank()) return
    Text(title, style = MaterialTheme.typography.labelMedium, color = Accent)
    Spacer(Modifier.height(2.dp))
    Text(body, style = MaterialTheme.typography.bodySmall)
    Spacer(Modifier.height(12.dp))
}

/* ================================================================== */
/* Small shared pieces                                                 */
/* ================================================================== */

@Composable
private fun ChatInput(
    value: String,
    onValue: (String) -> Unit,
    enabled: Boolean,
    placeholder: String,
    onSend: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextField(
            value = value,
            onValueChange = onValue,
            modifier = Modifier.weight(1f),
            placeholder = { Text(placeholder) },
            singleLine = true,
            shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onSend() }),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = MaterialTheme.colorScheme.surface,
                unfocusedContainerColor = MaterialTheme.colorScheme.surface,
            ),
        )
        Spacer(Modifier.width(8.dp))
        IconButton(onClick = onSend, enabled = enabled && value.isNotBlank()) {
            Icon(
                Icons.AutoMirrored.Filled.Send,
                contentDescription = "Send",
                tint = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun Bubble(text: String, fromUser: Boolean) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (fromUser) Arrangement.End else Arrangement.Start,
    ) {
        Text(
            text,
            modifier = Modifier
                .fillMaxWidth(0.88f)
                .background(
                    if (fromUser) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.surface,
                    RoundedCornerShape(14.dp),
                )
                .padding(12.dp),
            color = if (fromUser) Color.White else MaterialTheme.colorScheme.onSurface,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun Block(title: String, body: String) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(14.dp))
            .padding(16.dp),
    ) {
        Text(
            title.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            body.ifBlank { "Not stated in this paper." },
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun BulletBlock(title: String, items: List<String>) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface, RoundedCornerShape(14.dp))
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(14.dp))
            .padding(16.dp),
    ) {
        Text(
            title.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        if (items.isEmpty()) {
            Text("Not stated in this paper.", style = MaterialTheme.typography.bodyMedium)
        } else {
            items.forEach {
                Row {
                    Text("•  ", color = Accent)
                    Text(it, style = MaterialTheme.typography.bodyMedium)
                }
                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
private fun Banner(text: String, color: Color) {
    Text(
        text,
        modifier = Modifier
            .fillMaxWidth()
            .background(color.copy(alpha = 0.12f), RoundedCornerShape(10.dp))
            .padding(12.dp),
        style = MaterialTheme.typography.bodySmall,
        color = color,
    )
}

@Composable
private fun MetaText(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun CenteredSpinner() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun EmptyState(message: String = "Upload a research paper to get a structured " +
    "breakdown and a literature-checked list of what it left open.") {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Nothing here yet", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Text(
                message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
    }
}
